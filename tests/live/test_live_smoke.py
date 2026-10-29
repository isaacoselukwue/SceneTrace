"""Opt-in live smoke test against Meta Model API. Runs only with SCENETRACE_RUN_LIVE_TESTS=true.

Uses the bundled synthetic clip (a drawn animation, not real footage) and the normal budget caps.
Results are model outputs: the assertions check structure and safety rules, not accuracy.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scenetrace.api.app import create_app
from scenetrace.config import ROOT, Settings

pytestmark = [pytest.mark.live, pytest.mark.skipif(os.environ.get("SCENETRACE_RUN_LIVE_TESTS", "").lower() != "true",
                                                    reason="live provider tests are opt-in")]
CLIPS = {"pickup": ROOT / "samples" / "synthetic" / "synthetic_bench_pickup.mp4",
         "occlusion": ROOT / "samples" / "synthetic" / "synthetic_bench_occlusion.mp4"}


def _wait(client: TestClient, job_id: str, timeout: float = 600) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        if job["state"] not in ("queued", "running", "cancel_requested"):
            return job
        time.sleep(1)
    raise AssertionError("live job timed out")


@pytest.mark.parametrize("name,question", [("pickup", "Who picked up the bottle?"), ("occlusion", "Who took the bottle?")])
def test_live_upload_to_cited_answer(tmp_path: Path, name: str, question: str) -> None:
    data_dir = os.environ.get("SCENETRACE_LIVE_DATA_DIR") or str(tmp_path / "data")
    settings = Settings.load(ROOT, env={"SCENETRACE_DATA_DIR": data_dir, "SCENETRACE_ENABLE_LIVE_UPLOADS": "true",
                                        "SCENETRACE_ENABLE_SECONDARY_REVIEW": os.environ.get("SCENETRACE_ENABLE_SECONDARY_REVIEW", "true")})
    assert settings.live_available, "MODEL_API_KEY is not configured"
    with TestClient(create_app(settings)) as client:
        csrf = client.post("/api/v1/session", json={}).json()["csrf_token"]
        headers = {"X-CSRF-Token": csrf}
        with CLIPS[name].open("rb") as fh:
            clip = client.post("/api/v1/clips", headers=headers, files={"file": (CLIPS[name].name, fh, "video/mp4")},
                               data={"consent": "true", "display_name": f"Synthetic {name}"}).json()
        assert _wait(client, clip["normalise_job_id"])["state"] == "completed"
        acc = client.post(f"/api/v1/clips/{clip['clip_id']}/analyses", headers=headers, json={
            "target": {"kind": "concept", "phrase": "bottle", "frame_index": 0}, "mode": "live",
            "idempotency_key": f"live-{name}-{int(time.time())}"}).json()
        job = _wait(client, acc["job_id"])
        analysis = client.get(f"/api/v1/analyses/{acc['analysis_id']}").json()
        summary = {"clip": name, "job_state": job["state"], "error": job["error"], "status": analysis["status"],
                   "partial_reasons": analysis["partial_reasons"],
                   "events": [(e["current"]["record"]["event_id"], e["current"]["record"]["event_type"],
                               e["current"]["record"]["status"], e["current"]["record"]["actor_id"],
                               e["current"]["record"]["t_start_ms"], e["current"]["record"]["t_end_ms"],
                               e["current"]["record"]["reason_codes"]) for e in analysis["events"]],
                   "cold_cost_microusd": analysis["usage"]["total_microusd"], "cold_cost_status": analysis["usage"]["cost_status"],
                   "timings_ms": analysis["timings_ms"]}
        assert analysis["run_mode"] == "live" and analysis["status"] in ("completed", "partial"), summary
        kinds = [e[1] for e in summary["events"]]
        assert kinds[0] == "first_seen" and kinds[-1] == "last_seen"
        assert analysis["usage"]["total_microusd"] <= settings.defaults["analysis_cap_microusd"]
        q = client.post(f"/api/v1/analyses/{acc['analysis_id']}/questions", headers=headers,
                        json={"question": question, "idempotency_key": f"q-{name}-{int(time.time())}"}).json()
        _wait(client, q["job_id"], timeout=180)
        inv = client.get(f"/api/v1/investigations/{q['investigation_id']}").json()
        summary["answer"] = [s["text"] for s in inv["sentences"]]
        summary["answer_source"] = inv["answer_source"]
        summary["review"] = [inv["review_mode"], inv["review_verdict"]]
        summary["rejected_claims"] = inv["rejected_claims"]
        summary["question_cost_microusd"] = inv["current_request_cost_microusd"]
        summary["limits_used"] = inv["limits_used"]
        print("\nLIVE SUMMARY " + json.dumps(summary, indent=1))
        assert inv["status"] in ("completed", "partial") and inv["sentences"]
        if name == "occlusion":
            assert not any(e[2] == "supported" and e[1] == "pick_up" for e in summary["events"])
            assert not any(e[3] for e in summary["events"] if e[1] == "pick_up")
