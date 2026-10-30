"""Evidence frames for a candidate, taken from the original upload through the frame map.

Interaction candidates get up to six raw frames (two before, two during, two after).
Raw frames are never drawn on: each has a separate annotated copy with neutral
O1/P1 boxes, registered and hashed as a derived asset.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, cast

from PIL import Image, ImageDraw, ImageFont

from ..domain import Candidate, Evidence, EvidenceItem, FrameMap, Observation
from ..media.frames import extract_source_frames
from ..providers.base import CancelToken
from ..storage import Storage, sha256_file

COLOURS = {"O": (20, 184, 166), "P": (245, 158, 11), "Z": (99, 102, 241)}


def _pick(frames: list[int], times: dict[int, int], targets: list[int], preferred: set[int]) -> list[int]:
    chosen: list[int] = []
    for t in targets:
        pool = [f for f in frames if f not in chosen] or frames
        best = min(pool, key=lambda f: (f not in preferred, abs(times[f] - t)))
        if best not in chosen:
            chosen.append(best)
    return chosen


def sample_frames(candidate: Candidate, frame_map: FrameMap, observations: list[Observation]) -> list[tuple[int, str]]:
    times = {e.proxy_frame_index: e.t_ms for e in frame_map.entries}
    observed = {o.proxy_frame_index for o in observations if o.entity_id == "O1" and o.detection_state == "observed"}
    if candidate.candidate_type in ("first_seen", "last_seen"):
        return [(frame_map.frame_for_t_ms(candidate.t_start_ms), "observation")]
    w0, s, e, w1 = candidate.window_start_ms, candidate.t_start_ms, candidate.t_end_ms, candidate.window_end_ms
    before = [f for f, t in times.items() if w0 <= t < s]
    during = [f for f, t in times.items() if s <= t <= e]
    after = [f for f, t in times.items() if e < t <= w1]
    picks: list[tuple[int, str]] = []
    if before:
        picks += [(f, "before") for f in _pick(before, times, [w0 + (s - w0) // 3, w0 + 2 * (s - w0) // 3], observed)]
    if during:
        picks += [(f, "during") for f in _pick(during, times, [s, e], set())]
    if after:
        picks += [(f, "after") for f in _pick(after, times, [e + (w1 - e) // 3, e + 2 * (w1 - e) // 3], observed)]
    seen: set[int] = set()
    unique = []
    for frame, role in sorted(picks, key=lambda p: times[p[0]]):
        if frame not in seen:
            seen.add(frame)
            unique.append((frame, role))
    return unique


def _annotate(raw: Path, out: Path, boxes: list[tuple[str, list[int]]], scale_x: float, scale_y: float) -> None:
    image = Image.open(raw).convert("RGB")
    draw = ImageDraw.Draw(image)
    width = max(2, round(image.width / 400))
    label_height = max(14, round(image.width / 30))
    padding = max(3, label_height // 5)
    font = ImageFont.load_default(size=round(label_height * 0.72))
    for label, box in boxes:
        colour = COLOURS.get(label[0], (255, 255, 255))
        x0, y0, x1, y1 = box[0] * scale_x, box[1] * scale_y, box[2] * scale_x, box[3] * scale_y
        draw.rectangle([x0, y0, x1, y1], outline=colour, width=width)
        label_top = max(0, y0 - label_height)
        draw.rectangle([x0, label_top, x0 + draw.textlength(label, font=font) + 2 * padding, label_top + label_height], fill=colour)
        draw.text((x0 + padding, label_top + label_height / 2), label, fill=(15, 23, 42), font=font, anchor="lm")
    image.save(out, format="PNG")


def collect_evidence(*, analysis_id: str, owner_id: str, clip_id: str, candidate: Candidate, frame_map: FrameMap,
                     source_path: Path, source_sha256: str, observations: list[Observation], storage: Storage,
                     analysis_dir: Path, first_evidence_number: int, purpose: str = "initial",
                     cancel: CancelToken | None = None) -> list[EvidenceItem]:
    del purpose
    picks = sample_frames(candidate, frame_map, observations)
    if not picks:
        return []
    evidence_dir = analysis_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    staging = evidence_dir / f".extract-{candidate.candidate_id}-{first_evidence_number}"
    extracted = extract_source_frames(source_path, frame_map, [f for f, _ in picks], staging, cancel=cancel)
    sx = frame_map.source_width / frame_map.proxy_width
    sy = frame_map.source_height / frame_map.proxy_height
    shown = {"O1", *candidate.actor_candidates}
    by_frame: dict[int, list[tuple[str, list[int]]]] = {}
    for o in observations:
        if o.detection_state == "observed" and o.box and o.entity_id in shown:
            by_frame.setdefault(o.proxy_frame_index, []).append((o.entity_id, o.box))
    items: list[EvidenceItem] = []
    number = first_evidence_number
    for frame, role in picks:
        entry = frame_map.entry(frame)
        raw_path = evidence_dir / f"V{number}.png"
        shutil.copyfile(extracted[frame], raw_path)
        with Image.open(raw_path) as img:
            width, height = img.size
        raw_id = f"V{number}"
        raw_asset = storage.register_asset(owner_id=owner_id, clip_id=clip_id, analysis_id=analysis_id, path=raw_path,
                                           kind="evidence_frame", media_type="image/png", derived=False)
        items.append(EvidenceItem(
            evidence=Evidence(evidence_id=raw_id, source_sha256=source_sha256, asset_path=f"evidence/{raw_path.name}",
                              asset_sha256=sha256_file(raw_path), t_ms=entry.t_ms, role=cast(Any, role), derived=False),
            asset_id=raw_asset, candidate_id=candidate.candidate_id, kind="frame", proxy_frame_index=frame,
            source_frame_index=entry.source_frame_index, source_pts_ms=entry.source_pts_ms, width=width, height=height))
        number += 1
        annotated_path = evidence_dir / f"V{number}.png"
        _annotate(raw_path, annotated_path, sorted(by_frame.get(frame, [])), sx, sy)
        annotated_asset = storage.register_asset(owner_id=owner_id, clip_id=clip_id, analysis_id=analysis_id,
                                                 path=annotated_path, kind="evidence_annotated", media_type="image/png",
                                                 derived=True)
        items.append(EvidenceItem(
            evidence=Evidence(evidence_id=f"V{number}", source_sha256=source_sha256,
                              asset_path=f"evidence/{annotated_path.name}", asset_sha256=sha256_file(annotated_path),
                              t_ms=entry.t_ms, role=cast(Any, role), derived=True),
            asset_id=annotated_asset, candidate_id=candidate.candidate_id, kind="annotated", proxy_frame_index=frame,
            source_frame_index=entry.source_frame_index, source_pts_ms=entry.source_pts_ms, width=width, height=height))
        number += 1
    shutil.rmtree(staging, ignore_errors=True)
    return items


def raw_evidence_ids(items: list[EvidenceItem]) -> list[str]:
    return [i.evidence.evidence_id for i in items if not i.evidence.derived]
