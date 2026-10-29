"""SAM 3.1 adapter on Meta Model API: selection on one frame and whole-clip tracking.

Output is segmentation token text in the Responses stream. It is parsed with
Meta's meta_sam_parser, converted to half-open boxes in proxy pixels, and paired
with masks decoded to SVG paths. Coverage is complete only when the stream
reached response.completed; otherwise every frame without a record is unknown.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import multiprocessing
import re
import threading
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Literal

import meta_sam_parser as msp

from ..config import Settings
from ..db import Database
from ..domain import BoxPrompt, ConceptPrompt, Coverage, ProviderBox, ProxyVideo, SelectionCandidate, TrackingResult
from ..ids import new_id, utc_now
from ..logs import get
from .base import CallOutcome, ProviderError, RunContext
from .recording import dispatch_call
from .retry import with_one_retry
from .transport import StreamResult

log = get("sam")
PARSER_VERSION = "meta-sam-parser==0.1.0"
METADATA = {"mask_encoding": "one_bit", "include_confidence": "true"}
_TOKEN = re.compile(r"[MLZ]|-?\d+(?:\.\d+)?")


async def _events(events: list[dict[str, Any]]):
    for event in events:
        yield event


def parse_segmentation(events: list[dict[str, Any]], media: Literal["image", "video"]) -> Any:
    fmt = msp.video_segmentation_format() if media == "video" else msp.image_segmentation_format()

    async def run() -> Any:
        parsed = msp.parse_responses_stream(_events(events), fmt)
        final = parsed.final_result
        return await (final() if callable(final) else final)

    return asyncio.run(run())


def translate_svg_path(path: str, dx: float, dy: float) -> str:
    out: list[str] = []
    numbers: list[float] = []
    for token in _TOKEN.findall(path):
        if token in ("M", "L", "Z"):
            out.append(token)
            continue
        numbers.append(float(token))
        if len(numbers) == 2:
            x, y = numbers[0] + dx, numbers[1] + dy
            out.append(f"{x:g} {y:g}")
            numbers = []
    return "".join(out)


def to_provider_boxes(result: Any, *, width: int, height: int, with_masks: bool = True) -> tuple[list[ProviderBox], list[str]]:
    """Convert parser records to ProviderBox in proxy pixels. Returns (boxes, diagnostics).

    Masks are decoded only when requested: the arithmetic-coded payloads are slow to decode in pure
    Python, and person tracks only need boxes (the UI labels them as boxes, not pixel masks)."""
    diagnostics = [f"{d.code}@{d.line}" for d in result.diagnostics]
    masks = {(m.frame.frame_index if m.frame else 0, m.object_id): m for m in result.records if m.kind == "mask"}
    boxes: list[ProviderBox] = []
    seen: set[tuple[int, str]] = set()
    for rec in result.records:
        if rec.kind != "box":
            continue
        frame = rec.frame.frame_index if rec.frame else 0
        key = (frame, rec.object_id)
        if key in seen:
            continue  # deterministic de-duplication of repeated records
        seen.add(key)
        values = (rec.left, rec.top, rec.right, rec.bottom)
        if not all(math.isfinite(v) for v in values):
            diagnostics.append(f"NON_FINITE_BOX@{frame}")
            continue
        x0, y0, x1, y1 = math.floor(rec.left), math.floor(rec.top), math.ceil(rec.right), math.ceil(rec.bottom)
        # Clamp only round-off at the frame edge; grossly invalid boxes are rejected, not salvaged.
        if x0 < -1 or y0 < -1 or x1 > width + 1 or y1 > height + 1 or x1 <= x0 or y1 <= y0:
            diagnostics.append(f"INVALID_BOX@{frame}")
            continue
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(width, x1), min(height, y1)
        confidence = None if rec.confidence is None else int(round(rec.confidence * 10_000))
        svg = area = centroid = None
        mask = masks.get(key) if with_masks else None
        if mask is not None:
            try:
                raster = msp.decode_mask_to_raster(mask.mask)
                w, h = mask.mask.width, mask.mask.height
                total = sx = sy = 0
                for row in range(h):
                    line = raster[row * w:(row + 1) * w]
                    count = sum(line)
                    if count:
                        total += count
                        sy += count * (row + 0.5)
                        sx += sum(col + 0.5 for col, v in enumerate(line) if v)
                if total:
                    area = total
                    centroid = (int((mask.bounds.left + sx / total) * 1000), int((mask.bounds.top + sy / total) * 1000))
                svg = translate_svg_path(msp.decode_mask_to_svg_path(mask.mask), mask.bounds.left, mask.bounds.top) or None
            except Exception as exc:  # a broken mask keeps its box; the mask is simply absent
                diagnostics.append(f"MASK_DECODE_FAILED@{frame}:{type(exc).__name__}")
        boxes.append(ProviderBox(proxy_frame_index=frame, provider_object_id=str(rec.object_id), box=[x0, y0, x1, y1],
                                 frame_width=width, frame_height=height, confidence_bp=confidence, mask_svg=svg,
                                 mask_area_px=area, mask_centroid_milli=centroid))
    boxes.sort(key=lambda b: (b.proxy_frame_index, b.provider_object_id))
    return boxes, diagnostics


def _parse_convert(events: list[dict[str, Any]], media: str, width: int, height: int,
                   with_masks: bool) -> tuple[list[ProviderBox], list[str], str]:
    result = parse_segmentation(events, media)  # type: ignore[arg-type]
    boxes, diagnostics = to_provider_boxes(result, width=width, height=height, with_masks=with_masks)
    return boxes, diagnostics, str(getattr(result.outcome, "status", ""))


_POOL: ProcessPoolExecutor | None = None
_POOL_LOCK = threading.Lock()


def parse_in_process(events: list[dict[str, Any]], media: str, width: int, height: int,
                     with_masks: bool) -> tuple[list[ProviderBox], list[str], str]:
    """Run the CPU-heavy parse in a worker process so concurrent tracks decode in parallel."""
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn"))
        pool = _POOL
    return pool.submit(_parse_convert, events, media, width, height, with_masks).result()


def missing_ranges(observed_frames: set[int], start: int, end: int) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    run_start = None
    for f in range(start, end + 1):
        if f not in observed_frames:
            run_start = f if run_start is None else run_start
        elif run_start is not None:
            ranges.append((run_start, f - 1))
            run_start = None
    if run_start is not None:
        ranges.append((run_start, end))
    return ranges


class MetaSamTracker:
    def __init__(self, *, transport: Any, settings: Settings, db: Database) -> None:
        self.transport = transport
        self.settings = settings
        self.db = db
        self.model_id = settings.models["tracking"]
        self._upload_lock = threading.Lock()
        self._uploads: dict[str, str] = {}

    def _stream(self, ctx: RunContext, *, operation: str, body: dict[str, Any], bound: int,
                cost: Any) -> tuple[StreamResult, str]:
        def attempt(n: int) -> tuple[StreamResult, str]:
            def dispatch() -> CallOutcome:
                stream = self.transport.stream_response(body, cancel=ctx.cancel)
                actual = cost(stream.usage) if stream.terminal == "response.completed" and stream.usage else None
                return CallOutcome(stream, stream.usage, actual, stream.request_id, stream.response_id, stream.status)
            return dispatch_call(ctx, operation=operation, model_id=self.model_id, upper_bound_microusd=bound,
                                 dispatch=dispatch, attempt=n)
        return with_one_retry(ctx, attempt)

    def select_instances(self, ctx: RunContext, *, image_png: bytes, width: int, height: int, phrase: str,
                         proxy_frame_index: int) -> list[SelectionCandidate]:
        rates = self.settings.rates
        url = "data:image/png;base64," + base64.b64encode(image_png).decode("ascii")
        body = {"model": self.model_id, "store": False, "metadata": dict(METADATA), "input": [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": phrase}, {"type": "input_image", "image_url": url}]}]}
        stream, _call = self._stream(ctx, operation="sam_image", body=body, bound=rates.sam_image_cost(1),
                                     cost=lambda u: rates.sam_image_cost(int(u.get("images_processed") or 1)))
        if stream.terminal != "response.completed":
            raise ProviderError("SELECTION_INCOMPLETE", ambiguous=True, request_id=stream.request_id)
        boxes, _diag = to_provider_boxes(parse_segmentation(stream.events, "image"), width=width, height=height)
        return [SelectionCandidate(selection_id=new_id("sel"), proxy_frame_index=proxy_frame_index, box=b.box,
                                   provider_object_id=b.provider_object_id, confidence_bp=b.confidence_bp) for b in boxes]

    def _ensure_upload(self, ctx: RunContext, video: ProxyVideo) -> str:
        with self._upload_lock:  # target and person tracks start together; upload the proxy once
            key = f"{ctx.analysis_id}:{video.sha256}"
            if key not in self._uploads:
                self._uploads[key] = self._upload(ctx, video)
            return self._uploads[key]

    def _upload(self, ctx: RunContext, video: ProxyVideo) -> str:
        if ctx.run_mode == "live":
            row = self.db.one("SELECT provider_file_id FROM remote_files WHERE analysis_id = ? AND deleted_at IS NULL"
                              " AND delete_requested_at IS NULL", (ctx.analysis_id,))
            if row is not None:
                return str(row["provider_file_id"])

        def dispatch() -> CallOutcome:
            result = self.transport.upload_file(Path(video.path), media_type="video/mp4", expires_after_s=86_400)
            return CallOutcome(result.json, None, 0, result.request_id, result.json.get("id"), result.status)

        info, _call = dispatch_call(ctx, operation="file_upload", model_id="files", upper_bound_microusd=0,
                                    dispatch=dispatch, paid=False)
        file_id = str(info["id"])
        if ctx.run_mode == "live":
            expires = info.get("expires_at")
            with self.db.write() as conn:
                conn.execute("INSERT INTO remote_files(id, owner_id, clip_id, analysis_id, provider_file_id, purpose, created_at,"
                             " expires_at) VALUES (?, ?, ?, ?, ?, 'user_data', ?, ?)",
                             (new_id("rf"), ctx.owner_id, video.clip_id, ctx.analysis_id, file_id, utc_now(),
                              str(expires) if expires is not None else None))
        return file_id

    def track(self, ctx: RunContext, *, video: ProxyVideo, prompt: ConceptPrompt | BoxPrompt,
              role: Literal["target", "person"]) -> TrackingResult:
        rates = self.settings.rates
        file_id = self._ensure_upload(ctx, video)
        metadata = dict(METADATA)
        if isinstance(prompt, BoxPrompt):
            x0, y0, x1, y1 = prompt.box
            text = msp.build_object_prompt(size=(video.width, video.height), objects=[msp.PromptObject(
                id=prompt.object_id, prompts=[msp.FramePrompt(frame=prompt.frame_index, box=msp.PromptBox(x0, y0, x1, y1))])])
            if prompt.propagation != "both":
                metadata["propagation_direction"] = prompt.propagation
            concept = "selected object"
        else:
            text = prompt.phrase
            concept = prompt.phrase
        body = {"model": self.model_id, "store": False, "metadata": metadata, "input": [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": text}, {"type": "input_video", "file_id": file_id}]}]}
        request_key = hashlib.sha256(json.dumps({"role": role, "text": text, "proxy": video.sha256, "model": self.model_id,
                                                 "metadata": metadata}, sort_keys=True).encode()).hexdigest()[:32]
        stream, call_id = self._stream(ctx, operation="sam_video", body=body, bound=rates.sam_video_cost(video.frame_count),
                                       cost=lambda u: rates.sam_video_cost(int(u.get("video_frames_processed") or video.frame_count)))
        boxes, diagnostics, parsed_status = parse_in_process(stream.events, "video", video.width, video.height, role == "target")
        completed = stream.terminal == "response.completed" and parsed_status == "completed"
        last = video.frame_count - 1
        observed = {b.proxy_frame_index for b in boxes}
        coverage = Coverage(request_key=request_key, concept=concept, role=role, requested_start_frame=0,
                            requested_end_frame=last, terminal_status="completed" if completed else "incomplete",
                            missing_ranges=[] if completed else missing_ranges(observed, 0, last))
        raw = hashlib.sha256(json.dumps(stream.events, sort_keys=True).encode()).hexdigest()
        outcome = "replayed" if ctx.run_mode == "recorded_real" else ("completed" if completed else "incomplete")
        if not completed:
            log.warning("tracking stream incomplete", extra={"event_code": "TRACKING_INCOMPLETE", "model_id": self.model_id})
        return TrackingResult(request_key=request_key, provider_response_id=stream.response_id, model_id=self.model_id,
                              concept=concept, role=role, prompt_kind=prompt.kind, frame_map_digest=video.frame_map_sha256,
                              records=boxes, coverage=coverage, outcome=outcome, provider_call_ids=[call_id],
                              raw_digest=raw, parser_version=PARSER_VERSION, diagnostics=diagnostics)
