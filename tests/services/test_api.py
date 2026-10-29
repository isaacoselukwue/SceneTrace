"""API, sessions, jobs and the full fixture-mode flow through the real worker and pipeline."""
from __future__ import annotations

import dataclasses
import json
import shutil
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scenetrace.api.app import create_app
from scenetrace.config import ROOT, Settings

SAMPLE = ROOT / "samples" / "synthetic" / "synthetic_bench_pickup.mp4"
BOTTLE = [511, 361, 529, 405]


def _settings(tmp_path: Path, **env: str) -> Settings:
    return Settings.load(ROOT, env={"SCENETRACE_DATA_DIR": str(tmp_path / "data"), "SCENETRACE_MODE": "fixture",
                                    "MODEL_API_KEY": "", "SCENETRACE_DEMO_ACCESS_CODE": "", **env})


class Api:
    def __init__(self, client: TestClient) -> None:
        self.c = client
        self.csrf = client.post("/api/v1/session", json={}).json()["csrf_token"]

    def post(self, url: str, **kw):
        return self.c.post(url, headers={"X-CSRF-Token": self.csrf, **kw.pop("headers", {})}, **kw)

    def upload(self, path: Path = SAMPLE, consent: str = "true"):
        with path.open("rb") as fh:
            return self.post("/api/v1/clips", files={"file": ("clip.mp4", fh, "video/mp4")},
                             data={"consent": consent, "display_name": "Bench test"})

    def wait(self, job_id: str, timeout: float = 120) -> dict:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            job = self.c.get(f"/api/v1/jobs/{job_id}").json()
            if job["state"] not in ("queued", "running", "cancel_requested"):
                return job
            time.sleep(0.2)
        raise AssertionError(f"job {job_id} did not finish")


@pytest.fixture
def api(tmp_path: Path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        yield Api(client)


def _ready_clip(api: Api) -> dict:
    clip = api.upload()
    assert clip.status_code == 201, clip.text
    body = clip.json()
    assert api.wait(body["normalise_job_id"])["state"] == "completed"
    return api.c.get(f"/api/v1/clips/{body['clip_id']}").json()


def test_full_fixture_flow_upload_to_cited_answer_and_sealed_export(api: Api) -> None:
    clip = _ready_clip(api)
    assert clip["media_kind"] == "synthetic_render" and (clip["proxy_width"], clip["proxy_frame_count"]) == (960, 200)
    accepted = api.post(f"/api/v1/clips/{clip['clip_id']}/analyses", json={
        "target": {"kind": "box", "frame_index": 0, "proxy_width_px": 960, "proxy_height_px": 540, "xyxy_exclusive": BOTTLE},
        "zones": [], "mode": "fixture", "idempotency_key": "analysis-0001"}).json()
    job = api.wait(accepted["job_id"])
    assert job["state"] in ("completed", "partial"), job
    analysis = api.c.get(f"/api/v1/analyses/{accepted['analysis_id']}").json()
    assert analysis["run_mode"] == "fixture" and analysis["media_kind"] == "synthetic_render"
    kinds = [e["current"]["record"]["event_type"] for e in analysis["events"]]
    assert kinds[0] == "first_seen" and kinds[-1] == "last_seen" and "pick_up" in kinds
    statuses = {e["current"]["record"]["status"] for e in analysis["events"]}
    assert "rejected" in statuses and "supported" in statuses  # the fixture pass-by is rejected, the lift supported
    raw = [e for e in analysis["evidence"] if not e["derived"]]
    image = api.c.get(raw[0]["image_url"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    # Server-sent events replay from Last-Event-ID without duplicates.
    events = api.c.get(f"/api/v1/jobs/{accepted['job_id']}/events", headers={"Last-Event-ID": "2"}).text
    ids = [int(line[4:]) for line in events.splitlines() if line.startswith("id: ")]
    assert ids and ids[0] == 3 and ids == sorted(set(ids)) and "event: job." in events.splitlines()[-3]
    # Ask a question; the answer is compiled from validated claims with citations.
    q = api.post(f"/api/v1/analyses/{accepted['analysis_id']}/questions",
                 json={"question": "Who picked up the bottle?", "idempotency_key": "question-0001"}).json()
    assert api.wait(q["job_id"])["state"] == "completed"
    inv = api.c.get(f"/api/v1/investigations/{q['investigation_id']}").json()
    cited = [s for s in inv["sentences"] if s["citation"]]
    assert cited and all(s["citation"]["evidence_ids"] for s in cited)
    assert inv["run_mode"] == "fixture" and inv["review_verdict"] == "not_run"
    # Export, verify against the registry anchor, then tamper with one evidence byte.
    exp = api.post(f"/api/v1/analyses/{accepted['analysis_id']}/exports", json={"idempotency_key": "export-0001"}).json()
    assert api.wait(exp["job_id"])["state"] == "completed"
    export = api.c.get(f"/api/v1/exports/{exp['export_id']}").json()
    assert export["status"] == "sealed" and export["trusted_head"] == export["head_digest"]
    ok = api.post(f"/api/v1/exports/{exp['export_id']}/verify", json={}).json()
    assert (ok["integrity"], ok["anchored"], ok["anchor_source"]) == ("valid", True, "registry")
    wrong = api.post(f"/api/v1/exports/{exp['export_id']}/verify", json={"expected_head": "0" * 64}).json()
    assert wrong["integrity"] == "failed" and "TRUST_ANCHOR_MISMATCH" in wrong["errors"]
    svc = api.c.app.state.svc
    row = svc.db.one("SELECT rel_dir FROM exports WHERE id = ?", (exp["export_id"],))
    evidence_file = next(svc.storage.contained(row["rel_dir"]).joinpath("evidence").glob("V*.png"))
    evidence_file.write_bytes(evidence_file.read_bytes() + b"x")
    tampered = api.post(f"/api/v1/exports/{exp['export_id']}/verify", json={}).json()
    assert tampered["integrity"] == "failed" and any(e.startswith("FILE_HASH_MISMATCH") for e in tampered["errors"])
    zipped = api.c.get(export["download_url"])
    assert zipped.status_code == 200 and zipped.content[:2] == b"PK"


def test_range_requests_on_video(api: Api) -> None:
    clip = _ready_clip(api)
    part = api.c.get(clip["video_url"], headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100 and part.headers["content-range"].startswith("bytes 0-99/")
    assert api.c.get(clip["video_url"], headers={"Range": "bytes=999999999-"}).status_code == 416
    assert api.c.get(clip["video_url"]).headers["accept-ranges"] == "bytes"


def test_needs_selection_then_bind(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    from scenetrace.domain import SelectionCandidate
    from scenetrace.ids import new_id
    from scenetrace.providers.fixture_tracking import FixtureTracker

    def two(self, ctx, *, image_png, width, height, phrase, proxy_frame_index):
        return [SelectionCandidate(selection_id=new_id("sel"), proxy_frame_index=proxy_frame_index, box=b,
                                   provider_object_id=str(i), confidence_bp=9000) for i, b in enumerate([BOTTLE, [600, 361, 618, 405]])]
    monkeypatch.setattr(FixtureTracker, "select_instances", two)
    clip = _ready_clip(api)
    acc = api.post(f"/api/v1/clips/{clip['clip_id']}/analyses", json={
        "target": {"kind": "concept", "phrase": "bottle", "frame_index": 0}, "mode": "fixture",
        "idempotency_key": "analysis-sel-1"}).json()
    assert api.wait(acc["job_id"])["state"] == "needs_selection"
    analysis = api.c.get(f"/api/v1/analyses/{acc['analysis_id']}").json()
    assert analysis["status"] == "needs_selection" and len(analysis["selections"]) == 2
    assert api.c.get(analysis["selections"][0]["thumbnail_url"]).status_code == 200
    bound = api.post(f"/api/v1/analyses/{acc['analysis_id']}/selection",
                     json={"selection_id": analysis["selections"][0]["selection_id"], "idempotency_key": "bind-000001"}).json()
    assert api.wait(bound["job_id"])["state"] in ("completed", "partial")
    assert api.post(f"/api/v1/analyses/{acc['analysis_id']}/selection",
                    json={"selection_id": "sel_x", "idempotency_key": "bind-000002"}).status_code == 409


def test_auth_csrf_cross_owner_and_deletion(api: Api, tmp_path: Path) -> None:
    clip = _ready_clip(api)
    anon = TestClient(api.c.app)
    assert anon.get(f"/api/v1/clips/{clip['clip_id']}").status_code == 404  # no existence leak
    with api.c.app.state.svc.db.write() as conn:
        conn.execute("INSERT INTO sessions(id, owner_id, csrf_token, scope, created_at, expires_at) VALUES"
                     " ('sess_" + "9" * 32 + "', 'owner_other', 'tok', 'owner', 't', '2999-01-01T00:00:00Z')")
    other = TestClient(api.c.app, cookies={"st_session": "sess_" + "9" * 32})
    missing = other.get(f"/api/v1/clips/{clip['clip_id']}")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "NOT_FOUND"
    assert other.get(clip["video_url"]).status_code == 404
    no_csrf = api.c.post(f"/api/v1/clips/{clip['clip_id']}/analyses", json={})
    assert no_csrf.status_code == 403 and no_csrf.json()["error"]["code"] == "CSRF_REJECTED"
    assert anon.post("/api/v1/clips", files={"file": ("a.mp4", b"x")}).status_code == 401
    assert api.c.delete(f"/api/v1/clips/{clip['clip_id']}", headers={"X-CSRF-Token": api.csrf}).status_code == 204
    assert api.c.get(f"/api/v1/clips/{clip['clip_id']}").status_code == 404
    assert api.c.get(clip["video_url"]).status_code == 404


def test_upload_limits_consent_and_corrupt_media(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    small = dataclasses.replace(settings, defaults={**settings.defaults, "max_upload_bytes": 50_000})
    with TestClient(create_app(small)) as client:
        api = Api(client)
        too_big = api.upload()
        assert too_big.status_code == 413 and too_big.json()["error"]["code"] == "UPLOAD_TOO_LARGE"
        assert list((small.data_dir / "tmp").iterdir()) == []  # nothing left behind
        assert api.upload(consent="false").status_code in (400, 413)
        junk = tmp_path / "junk.mp4"
        junk.write_bytes(b"\x00not a video" * 100)
        bad = api.upload(junk).json()
        job = api.wait(bad["normalise_job_id"])
        assert job["state"] == "failed"
        assert client.get(f"/api/v1/clips/{bad['clip_id']}").json()["status"] == "rejected"
    with TestClient(create_app(settings)) as client:
        api = Api(client)
        no_consent = api.upload(consent="false")
        assert no_consent.status_code == 400 and no_consent.json()["error"]["code"] == "CONSENT_REQUIRED"


def test_mode_gating_idempotency_and_queue(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path), start_worker=False)) as client:
        api = Api(client)
        svc = client.app.state.svc
        clip = api.upload().json()
        from scenetrace.jobs.worker import Worker
        Worker(svc).run_job(dict(svc.db.one("SELECT * FROM jobs WHERE id = ?", (clip["normalise_job_id"],))))
        body = {"target": {"kind": "box", "frame_index": 0, "proxy_width_px": 960, "proxy_height_px": 540,
                           "xyxy_exclusive": BOTTLE}, "mode": "live", "idempotency_key": "live-00001"}
        live = api.post(f"/api/v1/clips/{clip['clip_id']}/analyses", json=body)
        assert live.status_code == 503 and live.json()["error"]["code"] == "LIVE_DISABLED"
        replay = api.post(f"/api/v1/clips/{clip['clip_id']}/analyses", json={**body, "mode": "recorded_real"})
        assert replay.status_code == 409 and replay.json()["error"]["code"] == "NO_RECORDING"
        first = api.post(f"/api/v1/clips/{clip['clip_id']}/analyses", json={**body, "mode": "fixture", "idempotency_key": "same-key-1"})
        again = api.post(f"/api/v1/clips/{clip['clip_id']}/analyses", json={**body, "mode": "fixture", "idempotency_key": "same-key-1"})
        assert first.status_code == again.status_code == 202 and first.json()["job_id"] == again.json()["job_id"]
        changed = dict(body, mode="fixture", idempotency_key="same-key-1", zones=[{"label": "tray", "polygon": [[0, 0], [100, 0], [100, 100]]}])
        assert api.post(f"/api/v1/clips/{clip['clip_id']}/analyses", json=changed).status_code == 409
        codes = [api.post(f"/api/v1/clips/{clip['clip_id']}/analyses", json={**body, "mode": "fixture",
                                                                           "idempotency_key": f"queue-key-{i}"}).status_code
                 for i in range(6)]
        assert 429 in codes
        bad_box = api.post(f"/api/v1/clips/{clip['clip_id']}/analyses", json={**body, "mode": "fixture", "idempotency_key": "bad-box-1",
                                                                           "target": {**body["target"], "proxy_width_px": 1920}})
        assert bad_box.status_code in (400, 429)


def test_restart_marks_running_jobs_interrupted_and_cancel_stops_work(tmp_path: Path) -> None:
    from scenetrace.errors import Cancelled
    from scenetrace.jobs.worker import Worker
    with TestClient(create_app(_settings(tmp_path), start_worker=False)) as client:
        api = Api(client)
        svc = client.app.state.svc
        with svc.db.write() as conn:
            conn.execute("INSERT INTO jobs(id, owner_id, kind, state, created_at) VALUES ('job_" + "7" * 32 + "', 'owner_local',"
                         " 'analysis', 'running', 't')")
        Worker(svc).reconcile()
        assert svc.db.one("SELECT state FROM jobs WHERE id = ?", ("job_" + "7" * 32,))["state"] == "interrupted"
        started = threading.Event()

        def blocking(_svc, job, token):
            started.set()
            while True:
                token.check()
                time.sleep(0.05)
        worker = Worker(svc, handlers={"normalise": blocking})
        client.app.state.worker.cancel = worker.cancel  # route cancellation to this worker
        clip = api.upload().json()
        job = dict(svc.db.one("SELECT * FROM jobs WHERE id = ?", (clip["normalise_job_id"],)))
        runner = threading.Thread(target=worker.run_job, args=(job,))
        runner.start()
        assert started.wait(10)
        with svc.db.write() as conn:
            conn.execute("UPDATE jobs SET state = 'running' WHERE id = ?", (job["id"],))
        assert api.post(f"/api/v1/jobs/{job['id']}/cancel").status_code == 202
        runner.join(10)
        assert svc.db.one("SELECT state FROM jobs WHERE id = ?", (job["id"],))["state"] == "cancelled"
        del Cancelled


def test_access_code_sessions(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path, SCENETRACE_DEMO_ACCESS_CODE="correct horse"), start_worker=False)) as client:
        assert client.post("/api/v1/session", json={}).json()["scope"] == "public"
        assert client.post("/api/v1/session", json={"access_code": "wrong"}).status_code == 401
        owner = client.post("/api/v1/session", json={"access_code": "correct horse"}).json()
        assert owner["scope"] == "owner" and owner["live_uploads_enabled"] is False
        codes = [client.post("/api/v1/session", json={"access_code": "wrong"}).status_code for _ in range(10)]
        assert 429 in codes
        caps = client.get("/api/v1/capabilities").json()
        assert caps["project_owner"] == "Isaac Oselukwue" and "live" not in caps["available_run_modes"]
        assert "MODEL_API_KEY" not in json.dumps(caps).replace("MODEL_API_KEY missing", "")


def test_replay_transport_never_touches_the_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from scenetrace.providers.base import RecordingMiss
    from scenetrace.providers.recording import Recording, RecordingTransport, ReplayTransport
    from scenetrace.providers.transport import HTTPResult

    class Fake:
        def create_response(self, body):
            return HTTPResult(200, {"id": "resp_1", "status": "completed", "echo": body["input"]}, "req-1", 3)

    rec = Recording(tmp_path / "rec")
    RecordingTransport(Fake(), rec).create_response({"model": "m", "input": "a"})  # type: ignore[arg-type]
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network used")))
    replay = ReplayTransport(rec)
    assert replay.create_response({"model": "m", "input": "a"}).json["echo"] == "a"
    with pytest.raises(RecordingMiss):
        replay.create_response({"model": "m", "input": "a"})  # recorded once, replayed once
    with pytest.raises(RecordingMiss):
        replay.create_response({"model": "m", "input": "different"})
    shutil.rmtree(tmp_path / "rec")


def test_replay_serves_requests_recorded_before_they_opted_out_of_provider_storage(tmp_path: Path) -> None:
    from scenetrace.providers.base import RecordingMiss
    from scenetrace.providers.recording import Recording, RecordingTransport, ReplayTransport
    from scenetrace.providers.transport import StreamResult

    completed = {"type": "response.completed", "response": {"id": "resp_sam", "usage": {"images_processed": 1}}}

    class Fake:
        def stream_response(self, body, *, cancel=None, on_event=None):
            return StreamResult(200, [completed], "response.completed", completed["response"], "resp_sam",
                                completed["response"]["usage"], "req-sam", 4)

    rec = Recording(tmp_path / "rec")
    request_without_flag = {"model": "sam-3.1", "input": "bottle"}
    RecordingTransport(Fake(), rec).stream_response(request_without_flag)  # type: ignore[arg-type]
    replay = ReplayTransport(rec)
    assert replay.stream_response(dict(request_without_flag, store=False)).response_id == "resp_sam"
    with pytest.raises(RecordingMiss):
        replay.stream_response(dict(request_without_flag, store=False, input="person"))
