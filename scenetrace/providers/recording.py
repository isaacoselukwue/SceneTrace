"""Record and replay at the transport boundary.

Live runs wrap MetaTransport in RecordingTransport, which stores every request
digest and response under the analysis directory. A recorded_real run uses
ReplayTransport, which serves those responses by request digest and never
opens a network connection. The same adapter code therefore runs in live and
replay modes; a replay miss raises RecordingMiss instead of calling a model.

Ledger handling: adapters call `dispatch_call`, which meters live attempts
through `metered_call` and writes zero-cost `replayed` ledger rows in replay.

Every request opts out of provider-side storage with `store: false`. SAM requests
recorded before they carried that flag were indexed without it; the flag only
controls retention, so replay falls back to the digest without it.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .base import CallOutcome, CancelToken, RecordingMiss, RunContext, metered_call
from .transport import HTTPResult, MetaTransport, StreamResult


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _pixels_key(data_url: str) -> str:
    import base64
    import io

    from PIL import Image
    try:
        image = Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1]))).convert("RGB")
    except Exception:  # noqa: BLE001 - an undecodable image is keyed by its bytes
        return "bytes:" + hashlib.sha256(data_url.encode()).hexdigest()
    return f"pixels:{image.width}x{image.height}:" + hashlib.sha256(image.tobytes()).hexdigest()


def _normalise(value: Any) -> Any:
    """Replace image data URLs with a hash of their decoded pixels for request matching."""
    if isinstance(value, str) and value.startswith("data:image/"):
        return _pixels_key(value)
    if isinstance(value, list):
        return [_normalise(v) for v in value]
    if isinstance(value, dict):
        return {k: _normalise(v) for k, v in value.items()}
    return value


def body_digest(kind: str, body: Any) -> str:
    return hashlib.sha256(kind.encode() + b"\n" + _canonical(_normalise(body))).hexdigest()


class Recording:
    """An append-only index of transport exchanges stored in one directory."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.index_path = directory / "index.jsonl"
        self._lock = threading.Lock()

    def entries(self) -> list[dict[str, Any]]:
        if not self.index_path.is_file():
            return []
        return [json.loads(line) for line in self.index_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def append(self, kind: str, digest: str, response: Any, meta: dict[str, Any]) -> None:
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            seq = len(self.entries()) + 1
            name = f"{seq:04d}-{kind.replace('.', '_')}.json"
            (self.directory / name).write_bytes(_canonical(response))
            entry = {"seq": seq, "kind": kind, "digest": digest, "response_file": name, **meta}
            with self.index_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")

    def has(self, kind: str, digest: str) -> bool:
        return any(e["kind"] == kind and e["digest"] == digest for e in self.entries())

    def lookup(self, kind: str, digest: str, *, occurrence: int) -> tuple[dict[str, Any], Any]:
        matches = [e for e in self.entries() if e["kind"] == kind and e["digest"] == digest]
        if occurrence >= len(matches):
            raise RecordingMiss("RECORDING_MISS", detail=f"{kind}:{digest[:12]}")
        entry = matches[occurrence]
        path = self.directory / entry["response_file"]
        if path.parent != self.directory or path.is_symlink():
            raise RecordingMiss("RECORDING_INVALID")
        return entry, json.loads(path.read_bytes())


class RecordingTransport:
    """Pass-through to the live transport that also records each exchange."""

    def __init__(self, inner: MetaTransport, recording: Recording) -> None:
        self.inner = inner
        self.recording = recording

    def upload_file(self, path: Path, *, media_type: str, expires_after_s: int | None = None) -> HTTPResult:
        digest = body_digest("files.upload", {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        result = self.inner.upload_file(path, media_type=media_type, expires_after_s=expires_after_s)
        self.recording.append("files.upload", digest, result.json, {"status": result.status, "request_id": result.request_id,
                                                                    "elapsed_ms": result.elapsed_ms})
        return result

    def delete_file(self, file_id: str) -> int:
        return self.inner.delete_file(file_id)

    def count_input_tokens(self, body: dict[str, Any]) -> HTTPResult:
        result = self.inner.count_input_tokens(body)
        self.recording.append("responses.input_tokens", body_digest("responses.input_tokens", body), result.json,
                              {"status": result.status, "request_id": result.request_id, "elapsed_ms": result.elapsed_ms})
        return result

    def create_response(self, body: dict[str, Any]) -> HTTPResult:
        result = self.inner.create_response(body)
        self.recording.append("responses.create", body_digest("responses.create", body), result.json,
                              {"status": result.status, "request_id": result.request_id, "elapsed_ms": result.elapsed_ms})
        return result

    def stream_response(self, body: dict[str, Any], *, cancel: CancelToken | None = None,
                        on_event: Callable[[dict[str, Any]], None] | None = None) -> StreamResult:
        result = self.inner.stream_response(body, cancel=cancel, on_event=on_event)
        self.recording.append("responses.stream", body_digest("responses.stream", dict(body, stream=True)),
                              {"events": result.events, "terminal": result.terminal},
                              {"status": result.status, "request_id": result.request_id, "elapsed_ms": result.elapsed_ms})
        return result


class ReplayTransport:
    """Serves recorded responses by request digest. Never opens a network connection."""

    def __init__(self, recording: Recording) -> None:
        self.recording = recording
        self._seen: dict[str, int] = {}
        self._lock = threading.Lock()

    def _next(self, kind: str, body: dict[str, Any]) -> tuple[dict[str, Any], Any]:
        digest = body_digest(kind, body)
        if "store" in body and not self.recording.has(kind, digest):
            digest = body_digest(kind, {key: value for key, value in body.items() if key != "store"})
        with self._lock:
            occurrence = self._seen.get(kind + digest, 0)
            self._seen[kind + digest] = occurrence + 1
        return self.recording.lookup(kind, digest, occurrence=occurrence)

    def upload_file(self, path: Path, *, media_type: str, expires_after_s: int | None = None) -> HTTPResult:
        entry, data = self._next("files.upload", {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        return HTTPResult(entry["status"], data, entry.get("request_id"), 0)

    def delete_file(self, file_id: str) -> int:
        return 200  # nothing remote exists for a replay

    def count_input_tokens(self, body: dict[str, Any]) -> HTTPResult:
        entry, data = self._next("responses.input_tokens", body)
        return HTTPResult(entry["status"], data, entry.get("request_id"), 0)

    def create_response(self, body: dict[str, Any]) -> HTTPResult:
        entry, data = self._next("responses.create", body)
        return HTTPResult(entry["status"], data, entry.get("request_id"), 0)

    def stream_response(self, body: dict[str, Any], *, cancel: CancelToken | None = None,
                        on_event: Callable[[dict[str, Any]], None] | None = None) -> StreamResult:
        entry, data = self._next("responses.stream", dict(body, stream=True))
        events = data["events"]
        for event in events:
            if on_event is not None:
                on_event(event)
        terminal = data.get("terminal")
        response = (events[-1].get("response") or {}) if terminal and events else {}
        return StreamResult(entry["status"], events, terminal, response, response.get("id"), response.get("usage"),
                            entry.get("request_id"), 0)


def dispatch_call(ctx: RunContext, *, operation: str, model_id: str, upper_bound_microusd: int,
                  dispatch: Callable[[], CallOutcome], attempt: int = 1, paid: bool = True) -> tuple[Any, str]:
    """Meter a live attempt, or record a zero-cost replay row in recorded_real mode."""
    if ctx.run_mode == "recorded_real":
        outcome = dispatch()  # ReplayTransport underneath; raises RecordingMiss on a miss
        call_id = ctx.ledger.record_replay(ctx, operation=operation, model_id=model_id, original_call={
            "provider_call_id": outcome.response_id, "actual_microusd": outcome.actual_microusd,
            "cost_status": "provider_usage_based" if outcome.usage is not None else "unknown", "usage": outcome.usage})
        return outcome.value, call_id
    if ctx.run_mode == "fixture":
        raise RuntimeError("fixture mode must not dispatch provider calls")
    return metered_call(ctx, operation=operation, model_id=model_id, upper_bound_microusd=upper_bound_microusd,
                        dispatch=dispatch, attempt=attempt, paid=paid)
