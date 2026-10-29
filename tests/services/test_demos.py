"""The published demonstrations replay offline and reproduce the outcomes of their recorded live runs."""
from __future__ import annotations

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
