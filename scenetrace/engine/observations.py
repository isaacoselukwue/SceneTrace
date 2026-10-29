"""Normalise provider tracking results into per-frame observations and clip-local entities.

The target response maps to O1. Person objects map to P1..Pn ordered by first
observed time, then centre x and y, then provider ordinal. Tracks from different
requests are never merged by equal integer IDs. Every requested frame gets an
explicit state: observed, not_detected (stream completed, no record) or unknown.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import pairwise
from typing import Any, cast

from ..domain import Coverage, Entity, FrameMap, Observation, ProviderBox, TrackingResult
from .config import DEFAULT_ENGINE_CONFIG, EngineConfig
from .geometry import centre


def _unknown_frames(coverage: Coverage) -> set[int]:
    if coverage.terminal_status == "completed":
        return set()
    frames: set[int] = set()
    for a, b in coverage.missing_ranges:
        frames.update(range(a, b + 1))
    if coverage.terminal_status in ("failed", "not_run"):
        frames.update(range(coverage.requested_start_frame, coverage.requested_end_frame + 1))
    return frames


def _centre_milli(record: ProviderBox) -> tuple[int, int]:
    if record.mask_centroid_milli is not None:
        return record.mask_centroid_milli
    cx, cy = centre(record.box)
    return int(cx * 1000), int(cy * 1000)


def _fragmented(frames: list[int], max_gap: int) -> bool:
    return any(b - a - 1 > max_gap for a, b in pairwise(frames))


def build_observations(*, analysis_id: str, clip_id: str, frame_map: FrameMap, results: list[TrackingResult],
                       config: EngineConfig = DEFAULT_ENGINE_CONFIG
                       ) -> tuple[list[Observation], list[Entity], list[Coverage]]:
    observations: list[Observation] = []
    entities: list[Entity] = []
    coverage: list[Coverage] = []
    t_of = {e.proxy_frame_index: e.t_ms for e in frame_map.entries}
    persons: list[tuple[tuple, TrackingResult, str, list[ProviderBox]]] = []
    for result in results:
        coverage.append(result.coverage)
        by_object: dict[str, list[ProviderBox]] = defaultdict(list)
        for record in result.records:
            by_object[record.provider_object_id].append(record)
        if result.role == "target":
            # A box prompt names the target as object 1; any other object is not the selected target.
            chosen = by_object.get("1") or (by_object[min(by_object)] if len(by_object) == 1 else [])
            _emit(observations, entities, analysis_id, clip_id, result, "1", "O1", "object", "Selected object",
                  chosen, frame_map, t_of, config)
        elif result.role == "person":
            for object_id, records in by_object.items():
                first = min(records, key=lambda r: r.proxy_frame_index)
                cx, cy = centre(first.box)
                ordinal = int(object_id) if object_id.isdigit() else 1 << 30
                persons.append(((first.proxy_frame_index, cx, cy, ordinal, result.request_key), result, object_id, records))
    for number, (_key, result, object_id, records) in enumerate(sorted(persons, key=lambda p: p[0]), start=1):
        _emit(observations, entities, analysis_id, clip_id, result, object_id, f"P{number}", "person", "Person track",
              records, frame_map, t_of, config)
    return observations, entities, coverage


def _emit(observations: list[Observation], entities: list[Entity], analysis_id: str, clip_id: str,
          result: TrackingResult, object_id: str, entity_id: str, kind: str, label: str, records: list[ProviderBox],
          frame_map: FrameMap, t_of: dict[int, int], config: EngineConfig) -> None:
    track_key = f"{clip_id}:{result.request_key}:{result.concept}:{object_id}"
    by_frame = {r.proxy_frame_index: r for r in records}
    unknown = _unknown_frames(result.coverage)
    cov = result.coverage
    observed_frames = sorted(by_frame)
    warning = _fragmented(observed_frames, config.max_gap_samples)
    for frame in range(cov.requested_start_frame, min(cov.requested_end_frame, len(frame_map.entries) - 1) + 1):
        record = by_frame.get(frame)
        if record is not None:
            state = "observed"
        elif frame in unknown or cov.terminal_status != "completed":
            state = "unknown"
        else:
            state = "not_detected"
        observations.append(Observation(
            analysis_id=analysis_id, request_key=result.request_key, entity_id=entity_id, track_key=track_key,
            proxy_frame_index=frame, t_ms=t_of[frame], width_px=frame_map.proxy_width, height_px=frame_map.proxy_height,
            box=list(record.box) if record else None, centre_milli=_centre_milli(record) if record else None,
            mask_svg=record.mask_svg if record else None, mask_area_px=record.mask_area_px if record else None,
            provider_confidence_bp=record.confidence_bp if record else None, detection_state=state,
            identity_warning=warning))
    entities.append(Entity(entity_id=entity_id, kind=cast(Any, kind), label=label, track_keys=[track_key],
                           first_t_ms=t_of[observed_frames[0]] if observed_frames else None,
                           last_t_ms=t_of[observed_frames[-1]] if observed_frames else None, identity_warning=warning))
