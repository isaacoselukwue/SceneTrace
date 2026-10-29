"""The published demonstrations replay offline and reproduce the outcomes of their recorded live runs."""
from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path

import httpx
import pytest

from scenetrace.config import ROOT
from scenetrace.demos import import_demos
from scenetrace.jobs.context import Services

from .test_api import _settings

RECORDED_EVENTS = {
    "Bench pickup and pass-by": [("E1", "first_seen", "supported", None), ("E2", "pick_up", "rejected", None),
                                 ("E3", "pick_up", "supported", "P2"), ("E4", "last_seen", "supported", None)],
    "Hidden removal": [("E1", "first_seen", "supported", None), ("E2", "pick_up", "plausible", None),
                       ("E3", "last_seen", "supported", None)],
}
ORIGINAL_COLD_MS = {"Bench pickup and pass-by": 40429, "Hidden removal": 25430}
RECORDED_REVIEW_VERDICTS = {
    "Bench pickup and pass-by": ["mixed", "pass", "not_run"],
    "Hidden removal": ["pass", "needs_evidence"],
}


def test_published_demos_replay_offline_with_their_recorded_outcomes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network used")))
    svc = Services.create(_settings(tmp_path))
    import_demos(svc, ROOT / "demos")
    demos = svc.db.query("SELECT d.title, a.id, a.status FROM demos d JOIN analyses a ON a.id = d.analysis_id")
    assert {demo["title"]: demo["status"] for demo in demos} == dict.fromkeys(RECORDED_EVENTS, "completed")
    for demo in demos:
        events = svc.store.current_events(demo["id"])
        replayed_events = [(event_id, revision.record.event_type, revision.record.status, revision.record.actor_id)
                           for event_id, revision in sorted(events.items())]
        assert replayed_events == RECORDED_EVENTS[demo["title"]]
        investigations = svc.db.query("SELECT status, review_verdict FROM investigations WHERE analysis_id = ? ORDER BY created_at",
                                      (demo["id"],))
        assert [row["status"] for row in investigations] == ["completed"] * len(RECORDED_REVIEW_VERDICTS[demo["title"]])
        assert [row["review_verdict"] for row in investigations] == RECORDED_REVIEW_VERDICTS[demo["title"]]
        cost_statuses = {row["cost_status"] for row in svc.db.query(
            "SELECT DISTINCT cost_status FROM provider_calls WHERE analysis_id = ?", (demo["id"],))}
        assert cost_statuses == {"replayed"}
        timings = json.loads(svc.store.analysis(demo["id"])["timings_json"])
        assert timings["total_ms"] == ORIGINAL_COLD_MS[demo["title"]] and timings["replay_total_ms"] > 0


def test_demo_listing_has_posters_and_visibility_shows_the_unseen_gap(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from scenetrace.api.app import create_app
    settings = _settings(tmp_path)
    import_demos(Services.create(settings), ROOT / "demos", replay_questions=False)
    with TestClient(create_app(settings)) as client:
        demos = {demo["title"]: demo for demo in client.get("/api/v1/demos").json()}
        for demo in demos.values():
            poster = client.get(demo["poster_url"])
            assert poster.status_code == 200 and poster.headers["content-type"] == "image/png"
        hidden = client.get(f"/api/v1/analyses/{demos['Hidden removal']['analysis_id']}/visibility").json()
        spans = hidden["spans"]
        assert spans[0]["state"] == "observed" and spans[0]["start_ms"] == 0
        assert all(previous["end_ms"] == following["start_ms"] for previous, following in pairwise(spans))
        assert spans[-1]["end_ms"] == hidden["duration_ms"]
        assert "not_detected" in [span["state"] for span in spans if span["start_ms"] >= 7_500]
