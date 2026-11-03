from __future__ import annotations

import hashlib
from typing import Literal

from ..domain import BoxPrompt, ConceptPrompt, Coverage, ProviderBox, ProxyVideo, SelectionCandidate, TrackingResult
from ..ids import new_id
from .base import RunContext

MODEL_ID = "fixture-tracker"


def _box(cx: float, cy: float, w: float, h: float, width: int, height: int) -> list[int] | None:
    x0, y0, x1, y1 = int(cx - w / 2), int(cy - h / 2), int(cx + w / 2), int(cy + h / 2)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(width, x1), min(height, y1)
    return [x0, y0, x1, y1] if x1 - x0 >= 2 and y1 - y0 >= 2 else None


class FixtureTracker:
    model_id = MODEL_ID

    def select_instances(self, ctx: RunContext, *, image_png: bytes, width: int, height: int, phrase: str,
                         proxy_frame_index: int) -> list[SelectionCandidate]:
        box = _box(width * 0.5, height * 0.62, width * 0.035, height * 0.09, width, height)
        assert box is not None
        return [SelectionCandidate(selection_id=new_id("sel"), proxy_frame_index=proxy_frame_index, box=box,
                                   provider_object_id="0", confidence_bp=None)]

    def track(self, ctx: RunContext, *, video: ProxyVideo, prompt: ConceptPrompt | BoxPrompt,
              role: Literal["target", "person"]) -> TrackingResult:
        n, W, H = video.frame_count, video.width, video.height
        seed = prompt.box if isinstance(prompt, BoxPrompt) else _box(W * 0.5, H * 0.62, W * 0.035, H * 0.09, W, H)
        assert seed is not None
        tx, ty = (seed[0] + seed[2]) / 2, (seed[1] + seed[3]) / 2
        tw, th = seed[2] - seed[0], seed[3] - seed[1]
        person_h = max(th * 5.5, H * 0.3)
        person_w = person_h * 0.3
        lift, leave = int(n * 0.55), int(n * 0.85)
        records: list[ProviderBox] = []

        def add(frame: int, obj: str, box: list[int] | None) -> None:
            if box is not None:
                records.append(ProviderBox(proxy_frame_index=frame, provider_object_id=obj, box=box, frame_width=W,
                                           frame_height=H, confidence_bp=None))

        for f in range(n):
            if role == "target":
                if f < lift:
                    add(f, "1", list(seed))
                elif f < leave:
                    p = (f - lift) / max(1, leave - lift)
                    add(f, "1", _box(tx + p * (W - tx + tw), ty - th * 0.8, tw, th, W, H))
            else:
                start, end = int(n * 0.15), int(n * 0.35)
                if start <= f <= end:
                    p = (f - start) / max(1, end - start)
                    add(f, "0", _box(-person_w + p * (W + 2 * person_w), ty + th - person_h / 2, person_w, person_h, W, H))
                arrive, stand = int(n * 0.42), int(n * 0.5)
                if arrive <= f < leave:
                    if f < stand:
                        x = -person_w + (f - arrive) / max(1, stand - arrive) * (tx + person_w * 0.9)
                    elif f < lift:
                        x = tx + person_w * 0.9
                    else:
                        x = tx + person_w * 0.9 + (f - lift) / max(1, leave - lift) * (W - tx + person_w)
                    add(f, "1", _box(x, ty + th - person_h / 2, person_w, person_h, W, H))
        key = hashlib.sha256(f"fixture:{role}:{video.sha256}:{seed}".encode()).hexdigest()[:32]
        coverage = Coverage(request_key=key, concept="selected object" if role == "target" else "person", role=role,
                            requested_start_frame=0, requested_end_frame=n - 1, terminal_status="completed")
        return TrackingResult(request_key=key, provider_response_id=None, model_id=MODEL_ID, concept=coverage.concept,
                              role=role, prompt_kind=prompt.kind, frame_map_digest=video.frame_map_sha256,
                              records=records, coverage=coverage, outcome="completed", provider_call_ids=[],
                              raw_digest=None, parser_version="fixture")
