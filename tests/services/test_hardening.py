from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scenetrace.api.app import create_app
from scenetrace.errors import AppError
from scenetrace.ids import utc_now
from scenetrace.providers.base import ProviderError

from .test_api import BOTTLE, Api, _ready_clip, _settings


@pytest.fixture
def api(tmp_path: Path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        yield Api(client)


def _analysis(api: Api, clip: dict, key: str = "hard-0001", zones: list | None = None) -> dict:
    accepted = api.post(f"/api/v1/clips/{clip['clip_id']}/analyses", json={
        "target": {"kind": "box", "frame_index": 0, "proxy_width_px": 960, "proxy_height_px": 540, "xyxy_exclusive": BOTTLE},
        "zones": zones or [], "mode": "fixture", "idempotency_key": key}).json()
    api.wait(accepted["job_id"])
    return accepted


def test_remote_cleanup_failure_is_retried_until_it_succeeds(api: Api) -> None:
    svc = api.c.app.state.svc
    clip = _ready_clip(api)
    accepted = _analysis(api, clip)
    with svc.db.write() as conn:
        conn.execute("INSERT INTO remote_files(id, owner_id, clip_id, analysis_id, provider_file_id, purpose, created_at)"
                     " VALUES ('rf_" + "1" * 32 + "', 'owner_local', ?, ?, 'file-remote-1', 'user_data', ?)",
                     (clip["clip_id"], accepted["analysis_id"], utc_now()))
    from scenetrace.jobs.cleanup import cleanup_remote_files

    class Flaky:
        def __init__(self) -> None:
            self.calls = 0

        def delete_file(self, file_id: str) -> int:
            self.calls += 1
            if self.calls == 1:
                raise ProviderError("backend_unavailable", http_status=503, retryable=True)
            return 200

    flaky = Flaky()
    assert cleanup_remote_files(svc, transport=flaky) == 0
    row = svc.db.one("SELECT * FROM remote_files WHERE provider_file_id = 'file-remote-1'")
    assert row["deleted_at"] is None and row["attempts"] == 1 and row["last_error"] == "backend_unavailable"
    assert cleanup_remote_files(svc, transport=flaky) == 1
    assert svc.db.one("SELECT deleted_at FROM remote_files WHERE provider_file_id = 'file-remote-1'")["deleted_at"]


def test_export_of_an_unfinished_run_is_refused(api: Api) -> None:
    svc = api.c.app.state.svc
    clip = _ready_clip(api)
    accepted = _analysis(api, clip)
    with svc.db.write() as conn:
        conn.execute("UPDATE analyses SET status = 'running' WHERE id = ?", (accepted["analysis_id"],))
    refused = api.post(f"/api/v1/analyses/{accepted['analysis_id']}/exports", json={"idempotency_key": "export-running"})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "ANALYSIS_NOT_READY"


def test_crafted_filenames_cannot_reach_the_filesystem(api: Api, tmp_path: Path) -> None:
    sample = Path(__file__).resolve().parents[2] / "samples" / "synthetic" / "synthetic_bench_pickup.mp4"
    with sample.open("rb") as fh:
        res = api.post("/api/v1/clips", files={"file": ("../../etc/passwd\x00.mp4", fh, "video/mp4")},
                       data={"consent": "true", "display_name": "<script>alert(1)</script>/../../x"})
    assert res.status_code == 201
    clip = res.json()
    assert "<" not in clip["display_name"] and "/" not in clip["display_name"]
    svc = api.c.app.state.svc
    stored = list(svc.storage.source_dir("owner_local", clip["clip_id"]).iterdir())
    assert [p.name for p in stored] == ["source.media"]


def test_sse_and_jobs_are_owner_scoped(api: Api) -> None:
    clip = _ready_clip(api)
    job_id = clip["normalise_job_id"]
    with api.c.app.state.svc.db.write() as conn:
        conn.execute("UPDATE jobs SET owner_id = 'owner_other' WHERE id = ?", (job_id,))
    assert api.c.get(f"/api/v1/jobs/{job_id}/events", headers={"Last-Event-ID": "0"}).status_code == 404
    assert api.c.get(f"/api/v1/jobs/{job_id}").status_code == 404
    assert api.post(f"/api/v1/jobs/{job_id}/cancel").status_code == 404


def test_a_session_for_an_owner_the_server_no_longer_recognises_is_rejected(api: Api) -> None:
    with api.c.app.state.svc.db.write() as conn:
        conn.execute("INSERT INTO sessions(id, owner_id, csrf_token, scope, created_at, expires_at) VALUES"
                     " ('sess_" + "8" * 32 + "', 'owner_other', 'tok', 'owner', 't', '2999-01-01T00:00:00Z')")
    other = TestClient(api.c.app, cookies={"st_session": "sess_" + "8" * 32})
    assert other.get("/api/v1/clips").status_code == 401
    assert other.post("/api/v1/clips", headers={"X-CSRF-Token": "tok"}).status_code == 401


def test_only_finished_live_runs_can_become_demos(api: Api, tmp_path: Path) -> None:
    from scenetrace.demos import publish_demo
    clip = _ready_clip(api)
    accepted = _analysis(api, clip)
    with pytest.raises(AppError) as exc:
        publish_demo(api.c.app.state.svc, accepted["analysis_id"], title="t", description="d", approved_by="x",
                     approval_note="n", out_dir=tmp_path / "demos")
    assert exc.value.code == "NOT_PUBLISHABLE"


def test_retention_expiry_revokes_access_and_removes_media(api: Api) -> None:
    svc = api.c.app.state.svc
    clip = _ready_clip(api)
    with svc.db.write() as conn:
        conn.execute("UPDATE clips SET expires_at = '2000-01-01T00:00:00Z' WHERE id = ?", (clip["clip_id"],))
    api.c.app.state.worker.maintain()
    assert svc.db.one("SELECT status FROM clips WHERE id = ?", (clip["clip_id"],))["status"] == "expired"
    assert api.c.get(f"/api/v1/clips/{clip['clip_id']}").status_code == 404
    assert api.c.get(clip["video_url"]).status_code == 404
    assert not svc.storage.clip_dir("owner_local", clip["clip_id"]).exists()


def test_upload_time_and_zones_are_recorded(api: Api) -> None:
    clip = _ready_clip(api)
    svc = api.c.app.state.svc
    assert svc.db.one("SELECT upload_ms FROM clips WHERE id = ?", (clip["clip_id"],))["upload_ms"] >= 0
    accepted = _analysis(api, clip, key="zones-0001", zones=[{"label": "tray", "polygon": [[700, 300], [820, 300], [820, 450], [700, 450]]}])
    analysis = api.c.get(f"/api/v1/analyses/{accepted['analysis_id']}").json()
    assert [(z["entity_id"], z["label"]) for z in analysis["zones"]] == [("Z1", "tray")]
    bad = api.post(f"/api/v1/clips/{clip['clip_id']}/analyses", json={
        "target": {"kind": "box", "frame_index": 0, "proxy_width_px": 960, "proxy_height_px": 540, "xyxy_exclusive": BOTTLE},
        "zones": [{"label": "tray", "polygon": [[0, 0], [2000, 0], [0, 10]]}], "mode": "fixture", "idempotency_key": "zones-bad-1"})
    assert bad.status_code == 400


def test_more_people_than_supported_marks_the_run_partial(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    from scenetrace.domain import ProviderBox
    from scenetrace.providers.fixture_tracking import FixtureTracker
    original = FixtureTracker.track

    def crowded(self, ctx, *, video, prompt, role):
        result = original(self, ctx, video=video, prompt=prompt, role=role)
        if role != "person":
            return result
        extra = [ProviderBox(proxy_frame_index=f, provider_object_id=str(10 + i), box=[40 * i, 100, 40 * i + 30, 300], frame_width=960,
                             frame_height=540, confidence_bp=None) for i in range(7) for f in range(5)]
        return result.model_copy(update={"records": result.records + extra})

    monkeypatch.setattr(FixtureTracker, "track", crowded)
    clip = _ready_clip(api)
    accepted = _analysis(api, clip, key="crowd-0001")
    analysis = api.c.get(f"/api/v1/analyses/{accepted['analysis_id']}").json()
    assert analysis["status"] == "partial" and "TOO_MANY_PEOPLE" in analysis["partial_reasons"]


def test_injected_assessor_output_is_invalid_not_trusted(tmp_path: Path) -> None:
    from scenetrace.domain import AssessmentResult, Candidate
    from scenetrace.investigator.assess import semantic_problems
    candidate = Candidate(candidate_id="K2", candidate_type="pick_up", t_start_ms=0, t_end_ms=100, window_start_ms=0,
                          window_end_ms=100, actor_candidates=["P1"])
    injected = AssessmentResult(candidate_id="K2", object_id="O1", visible_before="yes", transition_visible="yes",
                                visible_after="yes", support_changed="yes", carried_motion_visible="yes",
                                actor_candidates=["P1", "P9"], actor_assignment="P9", destination_zone="Z7",
                                evidence_ids=["V99"], verdict="supports", reason_codes=["CLEAR_LIFT"])
    problems = semantic_problems(injected, candidate, frames=[], persons={"P1"}, zones=set())
    assert {"UNKNOWN_EVIDENCE_ID", "UNKNOWN_ACTOR_ID", "UNKNOWN_ZONE_ID"} <= set(problems)


def test_client_assets_are_served_with_explicit_types(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import scenetrace.api.app as app_module
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>SceneTrace</title>")
    (dist / "assets" / "inter.woff2").write_bytes(b"wOF2")
    (dist / "assets" / "index.js").write_text("export {};")
    monkeypatch.setattr(app_module, "CLIENT_DIST", dist)
    with TestClient(create_app(_settings(tmp_path))) as client:
        assert client.get("/assets/inter.woff2").headers["content-type"] == "font/woff2"
        assert client.get("/assets/index.js").headers["content-type"].startswith("text/javascript")
        (tmp_path / "outside.txt").write_text("private outside the client directory")
        escaped = client.get("/assets/%2e%2e/%2e%2e/outside.txt")
        assert "private outside" not in escaped.text and escaped.text.startswith("<!doctype html>")
        assert client.get("/analysis/route").text.startswith("<!doctype html>")
