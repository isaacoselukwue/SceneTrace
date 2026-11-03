from __future__ import annotations

import argparse
import asyncio
import base64
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from importlib import metadata
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://api.meta.ai/v1"
MODELS = {"tracking": "sam-3.1", "investigator": "muse-spark-1.3", "reviewer": "muse-wonder-1.0"}
SPEND_CAP_MICROUSD = 400_000


def load_key() -> str:
    key = os.environ.get("MODEL_API_KEY", "")
    if not key:
        env = ROOT / ".env"
        if env.is_file():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("MODEL_API_KEY="):
                    key = line.split("=", 1)[1].strip()
    if not key:
        raise SystemExit("MODEL_API_KEY is not configured; live checks blocked")
    return key


def request_ids(headers: httpx.Headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if re.search(r"request|trace", k, re.I) and len(v) < 200}


def sanitise_error(resp: httpx.Response | None, exc: Exception | None = None) -> dict[str, Any]:
    if exc is not None:
        return {"category": type(exc).__name__}
    assert resp is not None
    try:
        body = resp.json().get("error", {})
    except (ValueError, AttributeError):
        body = {}
    return {"http_status": resp.status_code, "type": body.get("type"), "code": body.get("code"),
            "message": (body.get("message") or "")[:200]}


class Spike:
    def __init__(self, key: str, out_dir: Path) -> None:
        self.client = httpx.Client(base_url=BASE_URL, headers={"Authorization": f"Bearer {key}"},
                                   timeout=httpx.Timeout(connect=10, read=180, write=120, pool=10))
        self.checks: dict[str, dict[str, Any]] = {}
        self.out_dir = out_dir
        self.estimated_spend = 0

    def record(self, name: str, **fields: Any) -> None:
        self.checks[name] = fields
        print(f"[{fields.get('status')}] {name}", {k: v for k, v in fields.items() if k in ("elapsed_ms", "usage", "error", "detail")})

    def stream_events(self, body: dict[str, Any]) -> tuple[httpx.Response | None, list[dict[str, Any]], int]:
        started = time.monotonic()
        events: list[dict[str, Any]] = []
        with self.client.stream("POST", "/responses", json=body, headers={"Accept": "text/event-stream"}) as resp:
            if resp.status_code != 200:
                resp.read()
                return resp, events, int((time.monotonic() - started) * 1000)
            data_lines: list[str] = []
            for line in resp.iter_lines():
                if line == "":
                    if data_lines:
                        payload = "\n".join(data_lines)
                        data_lines = []
                        if payload.strip() == "[DONE]":
                            continue
                        events.append(json.loads(payload))
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip(" "))
            if data_lines and "".join(data_lines).strip() not in ("", "[DONE]"):
                events.append(json.loads("\n".join(data_lines)))
            return resp, events, int((time.monotonic() - started) * 1000)

    def check_models(self) -> None:
        started = time.monotonic()
        try:
            resp = self.client.get("/models")
        except httpx.HTTPError as exc:
            self.record("auth_and_model_listing", status="failed", error=sanitise_error(None, exc))
            return
        elapsed = int((time.monotonic() - started) * 1000)
        if resp.status_code != 200:
            self.record("auth_and_model_listing", status="failed", elapsed_ms=elapsed, error=sanitise_error(resp))
            return
        ids = sorted(m.get("id") for m in resp.json().get("data", []) if isinstance(m, dict))
        self.record("auth_and_model_listing", status="passed", elapsed_ms=elapsed, request_ids=request_ids(resp.headers),
                    listed_models=ids, required_listed={k: v in ids for k, v in MODELS.items()},
                    note="A listing alone does not establish image or video capability.")


async def _aiter(events: list[dict[str, Any]]):
    for event in events:
        yield event


def parse_sam(events: list[dict[str, Any]], media: str):
    import meta_sam_parser as msp
    fmt = msp.video_segmentation_format() if media == "video" else msp.image_segmentation_format()

    async def run():
        parsed = msp.parse_responses_stream(_aiter(events), fmt)
        async for _ in parsed:
            pass
        return await parsed.final_result() if callable(getattr(parsed, "final_result", None)) else parsed.final_result
    return asyncio.run(run())


def terminal_usage(events: list[dict[str, Any]]) -> tuple[str | None, dict[str, Any] | None, str | None]:
    for event in reversed(events):
        if event.get("type") in ("response.completed", "response.incomplete", "response.failed"):
            response = event.get("response") or {}
            return event["type"], response.get("usage"), response.get("id")
    return None, None, None


def frame_png(clip: Path, t_s: float, dest: Path) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(t_s), "-i", str(clip), "-frames:v", "1", str(dest)],
                   check=True, timeout=60)
    return dest


def data_url(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


ASSESS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "bottle_visible": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "person_count": {"type": "integer", "minimum": 0, "maximum": 10},
        "bottle_held_by_person": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "reason_codes": {"type": "array", "items": {"type": "string", "enum": ["CLEAR_LIFT", "STABLE", "POSSIBLE_OCCLUSION", "SMALL_TARGET", "NO_VISIBLE_TRANSITION"]}},
    },
    "required": ["bottle_visible", "person_count", "bottle_held_by_person", "reason_codes"],
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clip", type=Path, default=ROOT / "samples/synthetic/synthetic_bench_pickup.mp4")
    parser.add_argument("--out", type=Path, default=ROOT / "reports")
    parser.add_argument("--skip-wonder", action="store_true")
    args = parser.parse_args()
    key = load_key()
    fixtures = ROOT / "tests" / "fixtures" / "provider"
    fixtures.mkdir(parents=True, exist_ok=True)
    spike = Spike(key, args.out)
    started_at = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    spike.check_models()
    if spike.checks["auth_and_model_listing"]["status"] != "passed":
        write_report(spike, started_at, args.out)
        return
    tmp = Path(tempfile.mkdtemp(prefix="scenetrace-spike-"))
    still = frame_png(args.clip, 1.0, tmp / "still.png")
    lift = frame_png(args.clip, 14.2, tmp / "lift.png")

    body = {"model": MODELS["tracking"], "stream": True,
            "metadata": {"mask_encoding": "one_bit", "include_confidence": "true"},
            "input": [{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "bottle"}, {"type": "input_image", "image_url": data_url(still)}]}]}
    resp, events, elapsed = spike.stream_events(body)
    if resp is None or resp.status_code != 200:
        spike.record("sam_image", status="failed", elapsed_ms=elapsed, error=sanitise_error(resp))
    else:
        term, usage, rid = terminal_usage(events)
        result = parse_sam(events, "image")
        boxes = [r for r in result.records if r.kind == "box"]
        spike.record("sam_image", status="passed" if term == "response.completed" else "failed", elapsed_ms=elapsed,
                     request_ids=request_ids(resp.headers), response_id=rid, terminal_event=term, usage=usage,
                     detail={"boxes": [[b.left, b.top, b.right, b.bottom, b.confidence] for b in boxes],
                             "diagnostics": [d.code for d in result.diagnostics], "outcome": result.outcome.status})
        (fixtures / "sam_image_bottle.events.json").write_text(json.dumps(events), encoding="utf-8")

    proxy = tmp / "proxy.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(args.clip), "-vf", "fps=10", "-an", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(proxy)], check=True, timeout=300)
    file_id = None
    started = time.monotonic()
    with proxy.open("rb") as fh:
        up = spike.client.post("/files", data={"purpose": "user_data", "expires_after[anchor]": "created_at",
                                               "expires_after[seconds]": "3600"},
                               files={"file": ("proxy.mp4", fh, "video/mp4")})
    if up.status_code != 200:
        spike.record("files_upload", status="failed", elapsed_ms=int((time.monotonic() - started) * 1000), error=sanitise_error(up))
    else:
        info = up.json()
        file_id = info.get("id")
        spike.record("files_upload", status="passed", elapsed_ms=int((time.monotonic() - started) * 1000),
                     request_ids=request_ids(up.headers), detail={k: info.get(k) for k in ("object", "bytes", "purpose", "status", "expires_at")})
        for concept in ("bottle", "person"):
            body = {"model": MODELS["tracking"], "stream": True,
                    "metadata": {"mask_encoding": "one_bit", "include_confidence": "true"},
                    "input": [{"type": "message", "role": "user", "content": [
                        {"type": "input_text", "text": concept}, {"type": "input_video", "file_id": file_id}]}]}
            resp, events, elapsed = spike.stream_events(body)
            name = f"sam_video_{concept}"
            if resp is None or resp.status_code != 200:
                spike.record(name, status="failed", elapsed_ms=elapsed, error=sanitise_error(resp))
                continue
            term, usage, rid = terminal_usage(events)
            result = parse_sam(events, "video")
            boxes = [r for r in result.records if r.kind == "box"]
            frames = sorted({b.frame.frame_index for b in boxes if b.frame})
            objects = sorted({b.object_id for b in boxes})
            spike.record(name, status="passed" if term == "response.completed" else "failed", elapsed_ms=elapsed,
                         request_ids=request_ids(resp.headers), response_id=rid, terminal_event=term, usage=usage,
                         detail={"frames_with_records": len(frames), "first_frame": frames[0] if frames else None,
                                 "last_frame": frames[-1] if frames else None, "object_ids": objects,
                                 "delta_events": sum(1 for e in events if e.get("type") == "response.output_text.delta"),
                                 "event_types": sorted({e.get("type") for e in events}),
                                 "diagnostics": [d.code for d in result.diagnostics], "outcome": result.outcome.status})
            (fixtures / f"sam_video_{concept}.events.json").write_text(json.dumps(events), encoding="utf-8")

    content = [{"type": "input_text", "text": "Neutral check of a drawn test frame. Report only what is visible."},
               {"type": "input_image", "image_url": data_url(lift)}]
    count_body = {"model": MODELS["investigator"], "input": [{"type": "message", "role": "user", "content": content}]}
    started = time.monotonic()
    cnt = spike.client.post("/responses/input_tokens", json=count_body)
    if cnt.status_code == 200:
        spike.record("input_token_count_image", status="passed", elapsed_ms=int((time.monotonic() - started) * 1000),
                     detail=cnt.json(), request_ids=request_ids(cnt.headers))
    else:
        spike.record("input_token_count_image", status="failed", error=sanitise_error(cnt))

    for role in ("investigator", "reviewer"):
        if role == "reviewer" and args.skip_wonder:
            spike.record("muse_wonder_image_structured", status="not_tested", detail="skipped by flag")
            continue
        name = "muse_spark_image_structured" if role == "investigator" else "muse_wonder_image_structured"
        body = {"model": MODELS[role], "store": False, "max_output_tokens": 4000, "reasoning": {"effort": "low"},
                "input": [{"type": "message", "role": "user", "content": content}],
                "text": {"format": {"type": "json_schema", "name": "frame_check", "strict": True, "schema": ASSESS_SCHEMA}}}
        started = time.monotonic()
        try:
            resp = spike.client.post("/responses", json=body)
        except httpx.HTTPError as exc:
            spike.record(name, status="failed", error=sanitise_error(None, exc))
            continue
        elapsed = int((time.monotonic() - started) * 1000)
        if resp.status_code != 200:
            status = "unavailable" if resp.status_code in (403, 404) else "failed"
            spike.record(name, status=status, elapsed_ms=elapsed, error=sanitise_error(resp))
            continue
        data = resp.json()
        text = "".join(c.get("text", "") for item in data.get("output", []) if item.get("type") == "message"
                       for c in item.get("content", []) if c.get("type") == "output_text")
        try:
            parsed = json.loads(text)
            valid = set(parsed) == set(ASSESS_SCHEMA["properties"])
        except ValueError:
            parsed, valid = None, False
        spike.record(name, status="passed" if valid else "failed", elapsed_ms=elapsed, request_ids=request_ids(resp.headers),
                     response_id=data.get("id"), usage=data.get("usage"), detail={"parsed": parsed, "locally_valid": valid,
                                                                                  "response_status": data.get("status")})

    tools = [{"type": "function", "name": "read_history", "description": "Read stored revisions for the selected object. No provider work.",
              "strict": True, "parameters": {"type": "object", "properties": {"object_id": {"type": "string", "pattern": "^O[1-9][0-9]*$"}},
                                             "required": ["object_id"], "additionalProperties": False}}]
    body = {"model": MODELS["investigator"], "max_output_tokens": 2000, "reasoning": {"effort": "low"},
            "tool_choice": "auto", "parallel_tool_calls": False, "tools": tools,
            "instructions": "Use read_history before answering. Refer to objects only by supplied IDs.",
            "input": "What is the stored history of object O1?"}
    started = time.monotonic()
    first = spike.client.post("/responses", json=body)
    if first.status_code != 200:
        spike.record("tool_round_trip", status="failed", error=sanitise_error(first))
    else:
        data = first.json()
        calls = [i for i in data.get("output", []) if i.get("type") == "function_call"]
        if not calls:
            spike.record("tool_round_trip", status="failed", usage=data.get("usage"), detail="model replied without a function_call")
        else:
            call = calls[0]
            args_ok = json.loads(call.get("arguments") or "{}") == {"object_id": "O1"}
            follow = {"model": MODELS["investigator"], "max_output_tokens": 2000, "reasoning": {"effort": "low"},
                      "tool_choice": "auto", "parallel_tool_calls": False, "tools": tools, "previous_response_id": data["id"],
                      "input": [{"type": "function_call_output", "call_id": call["call_id"],
                                 "output": json.dumps({"object_id": "O1", "events": [{"event_id": "E1", "event_type": "first_seen", "t_start_ms": 0}]})}]}
            second = spike.client.post("/responses", json=follow)
            elapsed = int((time.monotonic() - started) * 1000)
            ok = second.status_code == 200
            d2 = second.json() if ok else {}
            spike.record("tool_round_trip", status="passed" if ok and args_ok else "failed", elapsed_ms=elapsed,
                         usage=[data.get("usage"), d2.get("usage")],
                         detail={"function_name": call.get("name"), "arguments_valid": args_ok, "call_status": call.get("status"),
                                 "second_status": d2.get("status"), "second_output_types": [i.get("type") for i in d2.get("output", [])]},
                         error=None if ok else sanitise_error(second))

    if file_id:
        dele = spike.client.delete(f"/files/{file_id}")
        after = spike.client.get(f"/files/{file_id}")
        spike.record("remote_file_cleanup", status="passed" if dele.status_code == 200 and after.status_code == 404 else "failed",
                     detail={"delete_status": dele.status_code, "retrieve_after_delete": after.status_code})
    write_report(spike, started_at, args.out)


def write_report(spike: Spike, started_at: str, out: Path) -> None:
    def version(pkg: str) -> str | None:
        try:
            return metadata.version(pkg)
        except metadata.PackageNotFoundError:
            return None
    report = {
        "checked_at": started_at,
        "base_url": BASE_URL,
        "python": sys.version.split()[0],
        "transport": {"httpx": version("httpx")},
        "parser": {"meta-sam-parser": version("meta-sam-parser")},
        "model_ids": MODELS,
        "media": "synthetic rendered test clip (not real footage)",
        "mode": "standard",
        "checks": spike.checks,
        "status_vocabulary": ["passed", "failed", "unavailable", "not_tested"],
    }
    out.mkdir(parents=True, exist_ok=True)
    path = out / "provider-capabilities.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("wrote", path.relative_to(ROOT))


if __name__ == "__main__":
    main()
