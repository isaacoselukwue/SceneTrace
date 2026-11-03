from __future__ import annotations

import json
from pathlib import Path

from scenetrace.budget import DurableLedger
from scenetrace.config import Settings
from scenetrace.db import Database
from scenetrace.domain import BoxPrompt, ConceptPrompt, FrameMap, FrameMapEntry, ProxyVideo
from scenetrace.providers.base import RunContext
from scenetrace.providers.transport import HTTPResult, StreamResult

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "provider"


def load_events(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text())


class FakeSamTransport:
    def __init__(self, *, target: str = "sam_video_bottle.events.json", person: str = "sam_video_person.events.json",
                 image: str = "sam_image_bottle.events.json", drop_terminal: bool = False) -> None:
        self.files = {"target": target, "person": person, "image": image}
        self.drop_terminal = drop_terminal
        self.calls: list[dict] = []

    def upload_file(self, path, *, media_type, expires_after_s=None):
        self.calls.append({"upload": str(path)})
        return HTTPResult(200, {"id": "file-test-1", "object": "file", "status": "uploaded"}, "req-up", 1)

    def stream_response(self, body, *, cancel=None, on_event=None):
        self.calls.append(body)
        content = body["input"][0]["content"]
        text = content[0]["text"]
        media = content[1]["type"]
        name = self.files["image"] if media == "input_image" else self.files["person" if text == "person" else "target"]
        events = load_events(name)
        if self.drop_terminal:
            events = [e for e in events if e.get("type") != "response.completed"]
        terminal = events[-1]["type"] if events[-1]["type"] == "response.completed" else None
        response = events[-1].get("response", {}) if terminal else {}
        return StreamResult(200, events, terminal, response, response.get("id"), response.get("usage"), "req-fake", 5)


def make_ctx(db: Database, settings: Settings, *, analysis_id: str = "ana_" + "c" * 32, run_mode: str = "live") -> RunContext:
    return RunContext(owner_id="owner_local", scope="cold", run_mode=run_mode, ledger=DurableLedger(db, settings),
                      analysis_id=analysis_id)


def hero_video() -> ProxyVideo:
    return ProxyVideo(clip_id="clip_" + "d" * 32, owner_id="owner_local", path="/nonexistent/proxy.mp4", sha256="e" * 64,
                      width=960, height=540, fps=10, frame_count=200, frame_map_sha256="f" * 64)


def hero_frame_map(frames: int = 200) -> FrameMap:
    return FrameMap(proxy_fps=10, proxy_width=960, proxy_height=540, source_width=960, source_height=540, rotation_deg=0,
                    source_time_base="1/12800", source_variable_frame_rate=False,
                    entries=[FrameMapEntry(proxy_frame_index=k, t_ms=k * 100, source_frame_index=(k * 5) // 2,
                                           source_pts=((k * 5) // 2) * 512, source_pts_ms=((k * 5) // 2) * 40)
                             for k in range(frames)])


def hero_tracking(db: Database, settings: Settings, **kw):
    from scenetrace.providers.meta_sam import MetaSamTracker
    transport = FakeSamTransport(**kw)
    tracker = MetaSamTracker(transport=transport, settings=settings, db=db)
    ctx = make_ctx(db, settings)
    with db.write() as conn:
        conn.execute("INSERT OR IGNORE INTO clips(id, owner_id, status, media_kind, display_name, consent_ack, source_sha256,"
                     " source_bytes, created_at) VALUES (?, 'owner_local', 'ready', 'synthetic_render', 'x', 1, ?, 1, 't')",
                     ("clip_" + "d" * 32, "a" * 64))
        conn.execute("INSERT OR IGNORE INTO analyses(id, owner_id, clip_id, run_mode, status, target_json, models_json, created_at)"
                     " VALUES (?, 'owner_local', ?, 'live', 'running', '{}', '{}', 't')", (ctx.analysis_id, "clip_" + "d" * 32))
    video = hero_video()
    target = tracker.track(ctx, video=video, prompt=BoxPrompt(frame_index=0, box=[511, 361, 529, 405]), role="target")
    person = tracker.track(ctx, video=video, prompt=ConceptPrompt(phrase="person"), role="person")
    return target, person, transport, ctx
