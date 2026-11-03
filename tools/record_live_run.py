from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from scenetrace.api.app import create_app  # noqa: E402
from scenetrace.config import Settings  # noqa: E402


def wait(client: TestClient, job_id: str, timeout: float = 900) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        if job["state"] not in ("queued", "running", "cancel_requested"):
            return job
        time.sleep(1)
    raise SystemExit(f"job {job_id} timed out")


def main() -> None:
    parser = argparse.ArgumentParser(description="Record a fresh live analysis of one clip, ask questions and print the IDs to publish as a demo. Spends real money against Meta Model API within the configured caps; use a fresh data directory.")
    parser.add_argument("--clip", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--phrase", required=True)
    parser.add_argument("--question", action="append", default=[])
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings.load(ROOT, env={"SCENETRACE_DATA_DIR": str(args.data_dir.resolve()), "SCENETRACE_ENABLE_LIVE_UPLOADS": "true",
                                        "SCENETRACE_ENABLE_SECONDARY_REVIEW": "true"})
    if not settings.live_available:
        raise SystemExit("MODEL_API_KEY is not configured; live recording is blocked")
    with TestClient(create_app(settings)) as client:
        headers = {"X-CSRF-Token": client.post("/api/v1/session", json={}).json()["csrf_token"]}
        with args.clip.open("rb") as fh:
            clip = client.post("/api/v1/clips", headers=headers, files={"file": (args.clip.name, fh, "video/mp4")},
                               data={"consent": "true", "display_name": args.name}).json()
        wait(client, clip["normalise_job_id"])
        stamp = int(time.time())
        acc = client.post(f"/api/v1/clips/{clip['clip_id']}/analyses", headers=headers, json={
            "target": {"kind": "concept", "phrase": args.phrase, "frame_index": 0}, "mode": "live",
            "idempotency_key": f"record-{stamp}"}).json()
        job = wait(client, acc["job_id"])
        analysis = client.get(f"/api/v1/analyses/{acc['analysis_id']}").json()
        print(json.dumps({"analysis_id": acc["analysis_id"], "job_state": job["state"], "status": analysis["status"],
                          "events": [(e["current"]["record"]["event_id"], e["current"]["record"]["event_type"],
                                      e["current"]["record"]["status"], e["current"]["record"]["actor_id"]) for e in analysis["events"]],
                          "cost_microusd": analysis["usage"]["total_microusd"], "timings_ms": analysis["timings_ms"]}, indent=1))
        for n, question in enumerate(args.question):
            q = client.post(f"/api/v1/analyses/{acc['analysis_id']}/questions", headers=headers,
                            json={"question": question, "idempotency_key": f"record-q{n}-{stamp}"}).json()
            wait(client, q["job_id"], timeout=300)
            inv = client.get(f"/api/v1/investigations/{q['investigation_id']}").json()
            print(json.dumps({"question": question, "answer": [s["text"] for s in inv["sentences"]], "source": inv["answer_source"],
                              "review": [inv["review_mode"], inv["review_verdict"]], "cost_microusd": inv["current_request_cost_microusd"]}))


if __name__ == "__main__":
    main()
