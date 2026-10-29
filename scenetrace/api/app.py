"""FastAPI application: one process serving the API, the job stream and the built client."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from python_multipart.multipart import MultipartParser, parse_options_header
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import logs
from ..config import Settings
from ..db import dumps, loads
from ..errors import AppError, not_found
from ..exports.bundle import build_export, verify_export
from ..ids import new_id, utc_now
from ..jobs.cleanup import cleanup_remote_files, delete_clip, expire_clips, expiry_for
from ..jobs.context import Services
from ..jobs.pipeline import new_investigation
from ..jobs.worker import Worker
from ..media.normalise import proxy_frame_png
from ..providers.factory import review_mode, reviewer_capability
from ..security import SESSION_COOKIE, RateLimiter, Session, Sessions, check_csrf
from . import schemas as S
from . import views

API = "/api/v1"
CLIENT_DIST = Path(__file__).resolve().parents[2] / "client" / "dist"
log = logs.get("api")


def _error(code: str, message: str, status: int, request: Request, retryable: bool = False) -> JSONResponse:
    rid = getattr(request.state, "request_id", "req_unknown")
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "retryable": retryable,
                                                               "request_id": rid}})


def create_app(settings: Settings | None = None, *, start_worker: bool = True) -> FastAPI:
    settings = settings or Settings.load()
    logs.configure()
    svc = Services.create(settings)
    sessions = Sessions(svc.db, settings)
    worker = Worker(svc)
    svc.worker = worker  # type: ignore[attr-defined]
    worker.handlers["export"] = build_export
    inference_limit = RateLimiter(30, 60)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        expire_clips(svc)
        if start_worker:
            worker.start()
            await asyncio.to_thread(cleanup_remote_files, svc)
        yield
        worker.stop()

    app = FastAPI(title="SceneTrace", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=f"{API}/openapi.json")
    app.state.svc, app.state.worker, app.state.sessions = svc, worker, sessions

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = new_id("req")
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError):
        return _error(exc.code, exc.message, exc.status_code, request, exc.retryable)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return _error("INVALID_ARGUMENT", "The request was not valid.", 400, request)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            return _error("NOT_FOUND", "The requested resource was not found.", 404, request)
        return _error("HTTP_ERROR", "The request could not be completed.", exc.status_code, request)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        log.error("unhandled error: %s", type(exc).__name__, extra={"request_id": getattr(request.state, "request_id", None)})
        return _error("INTERNAL_ERROR", "Something went wrong on the server.", 500, request)

    # Dependencies ---------------------------------------------------------------------------
    def session(request: Request) -> Session | None:
        return sessions.get(request.cookies.get(SESSION_COOKIE))

    def owner(request: Request, s: Session | None = Depends(session)) -> Session:
        if s is None or not s.is_owner:
            raise AppError("AUTH_REQUIRED", "Sign in with the owner access code to do this.", status_code=401)
        return s

    def mutation(request: Request, s: Session = Depends(owner), x_csrf_token: str | None = Header(default=None)) -> Session:
        check_csrf(s, x_csrf_token)
        origin = request.headers.get("origin")
        if origin and re.sub(r"^https?://", "", origin).rstrip("/") != request.headers.get("host", ""):
            raise AppError("CSRF_REJECTED", "Cross-origin requests are not accepted.", status_code=403)
        if settings.demo_read_only:
            raise AppError("DEMO_READ_ONLY", "This deployment serves recorded demonstrations only.", status_code=403)
        return s

    def readable(owner_id: str, s: Session | None) -> bool:
        return owner_id == "public" or (s is not None and s.owner_id == owner_id)

    def clip_row(clip_id: str, s: Session | None) -> dict:
        row = svc.db.one("SELECT * FROM clips WHERE id = ?", (clip_id,))
        if row is None or not readable(row["owner_id"], s) or row["status"] in ("deleted", "expired"):
            raise not_found("clip")
        return dict(row)

    def analysis_row(analysis_id: str, s: Session | None) -> dict:
        row = svc.db.one("SELECT a.*, c.status AS clip_status FROM analyses a JOIN clips c ON c.id = a.clip_id WHERE a.id = ?",
                         (analysis_id,))
        if row is None or not readable(row["owner_id"], s) or row["clip_status"] in ("deleted", "expired"):
            raise not_found("analysis")
        return dict(row)

    def job_row(job_id: str, s: Session | None) -> dict:
        row = svc.db.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if row is None or not readable(row["owner_id"], s):
            raise not_found("job")
        return dict(row)

    def enqueue(*, owner_id: str, kind: str, key: str | None, payload: dict | None, run_mode: str | None, **links: Any) -> tuple[str, bool]:
        digest = hashlib.sha256(dumps(payload or {}).encode()).hexdigest() if key else None
        with svc.db.write() as conn:
            if key:
                existing = conn.execute("SELECT id, payload_sha256 FROM jobs WHERE owner_id = ? AND kind = ? AND idempotency_key = ?",
                                        (owner_id, kind, key)).fetchone()
                if existing is not None:
                    if existing["payload_sha256"] != digest:
                        raise AppError("IDEMPOTENCY_CONFLICT", "That idempotency key was already used with different input.",
                                       status_code=409)
                    return existing["id"], False
            pending = conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE state IN ('queued', 'running', 'cancel_requested')").fetchone()["n"]
            if pending >= settings.limit("max_pending_jobs") + settings.limit("max_running_jobs"):
                raise AppError("QUEUE_FULL", "The analysis queue is full. Try again shortly.", status_code=429, retryable=True)
            job_id = new_id("job")
            conn.execute("INSERT INTO jobs(id, owner_id, kind, clip_id, analysis_id, investigation_id, export_id, state, run_mode,"
                         " idempotency_key, payload_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)",
                         (job_id, owner_id, kind, links.get("clip_id"), links.get("analysis_id"), links.get("investigation_id"),
                          links.get("export_id"), run_mode, key, digest, utc_now()))
        svc.events.append(job_id, "job.queued", {"summary": "Queued."}, mode=run_mode)
        return job_id, True

    def live_gate(s: Session) -> None:
        missing = []
        if not settings.model_api_key:
            missing.append("MODEL_API_KEY is not configured")
        if not settings.enable_live_uploads:
            missing.append("SCENETRACE_ENABLE_LIVE_UPLOADS is not true")
        if missing:
            raise AppError("LIVE_DISABLED", "Live inference is unavailable: " + "; ".join(missing) + ".", status_code=503)

    # Health, session and capabilities ---------------------------------------------------------
    @app.get(f"{API}/health", response_model=S.HealthOut)
    def health() -> S.HealthOut:
        return S.HealthOut(status="ok")

    @app.get(f"{API}/ready", response_model=S.ReadyOut)
    def ready() -> S.ReadyOut:
        db_ok = svc.db.one("SELECT 1 AS ok") is not None
        storage_ok = (settings.data_dir / "private").is_dir()
        ff = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
        alive = worker.alive or not start_worker
        return S.ReadyOut(status="ready" if all((db_ok, storage_ok, ff, alive)) else "degraded", database=db_ok,
                          storage=storage_ok, worker=alive, ffmpeg=ff)

    @app.post(f"{API}/session", response_model=S.SessionOut)
    def create_session(body: S.SessionCreate, request: Request, response: Response) -> S.SessionOut:
        s = sessions.create(body.access_code, request.client.host if request.client else "unknown")
        response.set_cookie(SESSION_COOKIE, s.id, httponly=True, samesite="strict", secure=settings.public_https, path="/",
                            max_age=12 * 3600)
        return S.SessionOut(scope=cast(Any, s.scope), csrf_token=s.csrf_token, expires_at=s.expires_at,
                            live_uploads_enabled=s.is_owner and settings.live_available and settings.enable_live_uploads)

    @app.delete(f"{API}/session", status_code=204)
    def delete_session(response: Response, s: Session = Depends(mutation)) -> Response:
        sessions.revoke(s.id)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return Response(status_code=204)

    @app.get(f"{API}/capabilities", response_model=S.CapabilitiesOut)
    def capabilities(s: Session | None = Depends(session)) -> S.CapabilitiesOut:
        report = {}
        try:
            report = json.loads((settings.root / "reports" / "provider-capabilities.json").read_text())
        except (OSError, ValueError):
            pass
        checks = report.get("checks", {})
        status_of = {"tracking": checks.get("sam_video_bottle", {}).get("status", "not_tested"),
                     "investigator": checks.get("muse_spark_image_structured", {}).get("status", "not_tested"),
                     "reviewer": reviewer_capability(settings)}
        modes: list[str] = ["fixture", "recorded_real"]
        reason = None
        if settings.live_available and settings.enable_live_uploads:
            modes.append("live")
        else:
            reason = "MODEL_API_KEY missing" if not settings.live_available else "Live uploads are disabled on this server"
        d = settings.defaults
        return S.CapabilitiesOut(
            project_owner="Isaac Oselukwue", default_run_mode=settings.default_mode, available_run_modes=cast(Any, modes),
            live_available="live" in modes, live_block_reason=reason, review_mode=cast(Any, review_mode(settings)),
            models=[S.ModelCapabilityOut(role=r, model_id=settings.models[r], status=cast(Any, status_of[r]),
                                         checked_at=report.get("checked_at")) for r in ("tracking", "investigator", "reviewer")],
            limits=S.LimitsOut(max_upload_bytes=d["max_upload_bytes"], max_clip_duration_ms=d["max_clip_duration_ms"],
                               max_supported_people=d["max_supported_people"], analysis_cap_microusd=d["analysis_cap_microusd"],
                               question_cap_microusd=d["question_cap_microusd"], daily_cap_microusd=d["daily_cap_microusd"],
                               max_pending_jobs=d["max_pending_jobs"]),
            demo_read_only=settings.demo_read_only, build={"version": "0.1.0", "commit": _commit(settings.root)})

    @app.get(f"{API}/demos", response_model=list[S.DemoOut])
    def demos() -> list[S.DemoOut]:
        out = []
        for d in svc.db.query("SELECT * FROM demos ORDER BY published_at"):
            a = svc.store.analysis(d["analysis_id"])
            clip = svc.store.clip(a["clip_id"])
            questions = [S.RecordedQuestionOut(investigation_id=r["id"], question=r["question"]) for r in svc.db.query(
                "SELECT id, question FROM investigations WHERE analysis_id = ? AND status IN ('completed', 'partial') ORDER BY created_at",
                (a["id"],))]
            usage = views.usage_out(svc.ledger.calls_for(analysis_id=a["recorded_from"] or a["id"]), a["rate_card_revision"])
            out.append(S.DemoOut(demo_id=d["id"], title=d["title"], description=d["description"], analysis_id=a["id"],
                                 clip_id=clip["id"], media_kind=clip["media_kind"], run_mode="recorded_real",
                                 recorded_at=a["recorded_at"], models=loads(a["models_json"], {}),
                                 original_processing_cost_microusd=usage.total_microusd, cost_status=usage.cost_status,
                                 questions=questions, poster_url=None))
        return out

    # Clips ----------------------------------------------------------------------------------------
    @app.post(f"{API}/clips", response_model=S.ClipOut, status_code=201)
    async def upload_clip(request: Request, s: Session = Depends(mutation)) -> S.ClipOut:
        upload_started = time.monotonic()
        cap = settings.limit("max_upload_bytes")
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > cap + 65_536:
            raise AppError("UPLOAD_TOO_LARGE", "The file is larger than the 150 MB limit.", status_code=413)
        content_type, params = parse_options_header(request.headers.get("content-type", ""))
        if content_type != b"multipart/form-data" or b"boundary" not in params:
            raise AppError("INVALID_ARGUMENT", "Send the clip as multipart form data.", status_code=400)
        tmp = svc.storage.tmp_dir()
        target = tmp / "upload.media"
        state: dict[str, Any] = {"headers": {}, "field": None, "name": None, "fields": {}, "bytes": 0, "too_big": False,
                                 "hasher": hashlib.sha256(), "fh": target.open("wb"), "key": b"", "value": b""}

        def on_header_field(data: bytes, start: int, end: int) -> None:
            state["key"] += data[start:end]

        def on_header_value(data: bytes, start: int, end: int) -> None:
            state["value"] += data[start:end]

        def on_header_end() -> None:
            state["headers"][state["key"].decode("latin-1").lower()] = state["value"]
            state["key"], state["value"] = b"", b""

        def on_headers_finished() -> None:
            _disp, opts = parse_options_header(state["headers"].get("content-disposition", b""))
            state["field"] = (opts.get(b"name") or b"").decode("latin-1")
            state["fields"].setdefault(state["field"], b"")

        def on_part_data(data: bytes, start: int, end: int) -> None:
            chunk = data[start:end]
            if state["field"] == "file":
                state["bytes"] += len(chunk)
                if state["bytes"] > cap:
                    state["too_big"] = True
                    return
                state["hasher"].update(chunk)
                state["fh"].write(chunk)
            elif len(state["fields"][state["field"]]) < 1000:
                state["fields"][state["field"]] += chunk

        def on_part_begin() -> None:
            state["headers"] = {}

        parser = MultipartParser(params[b"boundary"], {"on_part_begin": on_part_begin, "on_header_field": on_header_field,
                                                        "on_header_value": on_header_value, "on_header_end": on_header_end,
                                                        "on_headers_finished": on_headers_finished, "on_part_data": on_part_data})
        received = 0
        try:
            async for chunk in request.stream():
                received += len(chunk)
                if received > cap + 65_536 or state["too_big"]:
                    raise AppError("UPLOAD_TOO_LARGE", "The file is larger than the 150 MB limit.", status_code=413)
                parser.write(chunk)
            parser.finalize()
        except AppError:
            state["fh"].close()
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        except Exception as exc:  # noqa: BLE001 - malformed multipart bodies are client errors
            state["fh"].close()
            shutil.rmtree(tmp, ignore_errors=True)
            raise AppError("INVALID_ARGUMENT", "The upload could not be read.", status_code=400) from exc
        state["fh"].close()
        if state["too_big"]:
            shutil.rmtree(tmp, ignore_errors=True)
            raise AppError("UPLOAD_TOO_LARGE", "The file is larger than the 150 MB limit.", status_code=413)
        fields = {k: v.decode("utf-8", "replace").strip() for k, v in state["fields"].items() if k != "file"}
        if fields.get("consent") != "true":
            shutil.rmtree(tmp, ignore_errors=True)
            raise AppError("CONSENT_REQUIRED", "Confirm you own the footage and have permission from the people shown.", status_code=400)
        if state["bytes"] == 0:
            shutil.rmtree(tmp, ignore_errors=True)
            raise AppError("INVALID_ARGUMENT", "No video file was received.", status_code=400)
        digest = state["hasher"].hexdigest()
        name = re.sub(r"[^A-Za-z0-9 ._-]", "", fields.get("display_name", ""))[:60].strip() or "Uploaded clip"
        clip_id = new_id("clip")
        source_dir = svc.storage.source_dir(s.owner_id, clip_id)
        source_dir.mkdir(parents=True)
        shutil.move(str(target), source_dir / "source.media")
        shutil.rmtree(tmp, ignore_errors=True)
        kind = "synthetic_render" if digest in svc.samples else "user_upload"
        with svc.db.write() as conn:
            conn.execute("INSERT INTO clips(id, owner_id, status, media_kind, display_name, consent_ack, source_sha256, source_bytes,"
                         " created_at, expires_at, upload_ms) VALUES (?, ?, 'uploaded', ?, ?, 1, ?, ?, ?, ?, ?)",
                         (clip_id, s.owner_id, kind, name, digest, state["bytes"], utc_now(),
                          expiry_for(settings.limit("private_retention_hours")), int((time.monotonic() - upload_started) * 1000)))
        enqueue(owner_id=s.owner_id, kind="normalise", key=None, payload=None, run_mode=None, clip_id=clip_id)
        return views.clip_out(svc, clip_row(clip_id, s))

    @app.get(f"{API}/clips", response_model=list[S.ClipOut])
    def list_clips(s: Session = Depends(owner)) -> list[S.ClipOut]:
        rows = svc.db.query("SELECT * FROM clips WHERE owner_id = ? AND status NOT IN ('deleted', 'expired') ORDER BY created_at DESC",
                            (s.owner_id,))
        return [views.clip_out(svc, dict(r)) for r in rows]

    @app.get(f"{API}/clips/{{clip_id}}", response_model=S.ClipOut)
    def get_clip(clip_id: str, s: Session | None = Depends(session)) -> S.ClipOut:
        return views.clip_out(svc, clip_row(clip_id, s))

    @app.get(f"{API}/clips/{{clip_id}}/video")
    def clip_video(clip_id: str, s: Session | None = Depends(session)) -> FileResponse:
        clip = clip_row(clip_id, s)
        if clip["status"] != "ready":
            raise not_found("video")
        path = svc.storage.proxy_dir(clip["owner_id"], clip_id) / "proxy.mp4"
        return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "private, max-age=3600"})

    @app.get(f"{API}/clips/{{clip_id}}/frame", response_model=S.FrameOut)
    def clip_frame(clip_id: str, t_ms: int, s: Session | None = Depends(session)) -> S.FrameOut:
        clip = clip_row(clip_id, s)
        if clip["status"] != "ready" or not 0 <= t_ms <= (clip["duration_ms"] or 0):
            raise AppError("INVALID_ARGUMENT", "That time is outside the clip.", status_code=400)
        frame = min(round(t_ms * clip["proxy_fps"] / 1000), clip["proxy_frame_count"] - 1)
        out = svc.storage.proxy_dir(clip["owner_id"], clip_id) / "frames" / f"f{frame:05d}.png"
        if not out.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(proxy_frame_png(svc.storage.proxy_dir(clip["owner_id"], clip_id) / "proxy.mp4", frame,
                                            fps=clip["proxy_fps"]))
        asset = svc.storage.register_asset(owner_id=clip["owner_id"], clip_id=clip_id, path=out, kind="preview_frame",
                                           media_type="image/png", derived=True, public=clip["owner_id"] == "public")
        return S.FrameOut(t_ms=round(frame * 1000 / clip["proxy_fps"]), proxy_frame_index=frame, width=clip["proxy_width"],
                          height=clip["proxy_height"], image_url=views.asset_url(asset) or "")

    @app.delete(f"{API}/clips/{{clip_id}}", status_code=204)
    def remove_clip(clip_id: str, s: Session = Depends(mutation)) -> Response:
        clip = clip_row(clip_id, s)
        if clip["owner_id"] != s.owner_id:
            raise not_found("clip")
        for row in svc.db.query("SELECT id FROM jobs WHERE (clip_id = ? OR analysis_id IN (SELECT id FROM analyses WHERE clip_id = ?))"
                                " AND state IN ('queued', 'running')", (clip_id, clip_id)):
            worker.cancel(row["id"])
        delete_clip(svc, clip)
        return Response(status_code=204)

    # Analyses --------------------------------------------------------------------------------------
    @app.post(f"{API}/clips/{{clip_id}}/analyses", response_model=S.JobAccepted, status_code=202)
    def create_analysis(clip_id: str, body: S.AnalysisCreate, s: Session = Depends(mutation)) -> S.JobAccepted:
        clip = clip_row(clip_id, s)
        if clip["owner_id"] != s.owner_id:
            raise AppError("DEMO_READ_ONLY", "Recorded demonstrations cannot be re-analysed.", status_code=403)
        if clip["status"] != "ready":
            raise AppError("CLIP_NOT_READY", "The clip is still being prepared.", status_code=409, retryable=True)
        inference_limit.check(f"inference:{s.owner_id}")
        recorded_from = None
        if body.mode == "live":
            live_gate(s)
        elif body.mode == "recorded_real":
            source = svc.db.one("SELECT id FROM analyses WHERE clip_id = ? AND run_mode = 'live' AND status IN ('completed', 'partial')"
                                " ORDER BY created_at DESC LIMIT 1", (clip_id,))
            if source is None:
                raise AppError("NO_RECORDING", "There is no recorded real run for this clip.", status_code=409)
            recorded_from = source["id"]
        target = body.target.model_dump()
        if body.target.kind == "box":
            x0, y0, x1, y1 = body.target.xyxy_exclusive
            if (body.target.proxy_width_px, body.target.proxy_height_px) != (clip["proxy_width"], clip["proxy_height"]) \
                    or not (0 <= x0 < x1 <= clip["proxy_width"] and 0 <= y0 < y1 <= clip["proxy_height"]):
                raise AppError("INVALID_ARGUMENT", "The box does not match the clip's frame size.", status_code=400)
        if target.get("frame_index", 0) >= (clip["proxy_frame_count"] or 1):
            raise AppError("INVALID_ARGUMENT", "That frame is outside the clip.", status_code=400)
        for zone in body.zones:
            if any(not (0 <= x <= clip["proxy_width"] and 0 <= y <= clip["proxy_height"]) for x, y in zone.polygon):
                raise AppError("INVALID_ARGUMENT", "A zone point lies outside the frame.", status_code=400)
            pts = zone.polygon
            area = abs(sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))) / 2
            if area < 16:
                raise AppError("INVALID_ARGUMENT", "A zone is too small.", status_code=400)
        payload = body.model_dump(exclude={"idempotency_key"}) | {"clip_id": clip_id}
        existing = svc.db.one("SELECT id, analysis_id, payload_sha256 FROM jobs WHERE owner_id = ? AND kind = 'analysis' AND idempotency_key = ?",
                              (s.owner_id, body.idempotency_key))
        if existing is not None:
            job_id, _ = enqueue(owner_id=s.owner_id, kind="analysis", key=body.idempotency_key, payload=payload, run_mode=body.mode)
            return S.JobAccepted(job_id=job_id, analysis_id=existing["analysis_id"], state="queued")
        analysis_id = new_id("ana")
        models = settings.models if body.mode != "fixture" else {"tracking": "fixture-tracker", "investigator": "fixture-investigator"}
        with svc.db.write() as conn:
            conn.execute("INSERT INTO analyses(id, owner_id, clip_id, run_mode, status, target_json, zones_json, models_json,"
                         " recorded_from, recorded_at, created_at) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?)",
                         (analysis_id, s.owner_id, clip_id, body.mode, dumps(target), dumps([z.model_dump() for z in body.zones]),
                          dumps(models), recorded_from,
                          svc.db.require("SELECT completed_at FROM analyses WHERE id = ?", (recorded_from,))["completed_at"] if recorded_from else None,
                          utc_now()))
        job_id, _ = enqueue(owner_id=s.owner_id, kind="analysis", key=body.idempotency_key, payload=payload, run_mode=body.mode,
                            analysis_id=analysis_id, clip_id=clip_id)
        return S.JobAccepted(job_id=job_id, analysis_id=analysis_id, state="queued")

    @app.post(f"{API}/analyses/{{analysis_id}}/selection", response_model=S.JobAccepted, status_code=202)
    def bind_selection(analysis_id: str, body: S.SelectionBind, s: Session = Depends(mutation)) -> S.JobAccepted:
        a = analysis_row(analysis_id, s)
        if a["owner_id"] != s.owner_id:
            raise not_found("analysis")
        payload = {"selection_id": body.selection_id, "analysis_id": analysis_id}
        prior = svc.db.one("SELECT id, payload_sha256 FROM jobs WHERE owner_id = ? AND kind = 'analysis' AND idempotency_key = ?",
                           (s.owner_id, body.idempotency_key))
        if prior is None:
            if a["status"] != "needs_selection":
                raise AppError("STATE_CONFLICT", "This analysis is not waiting for a selection.", status_code=409)
            if svc.db.one("SELECT 1 FROM selections WHERE id = ? AND analysis_id = ?", (body.selection_id, analysis_id)) is None:
                raise AppError("INVALID_ARGUMENT", "Choose one of the offered selections.", status_code=400)
            with svc.db.write() as conn:
                conn.execute("UPDATE analyses SET selected_selection_id = ?, status = 'queued' WHERE id = ?", (body.selection_id, analysis_id))
        job_id, _ = enqueue(owner_id=s.owner_id, kind="analysis", key=body.idempotency_key, payload=payload, run_mode=a["run_mode"],
                            analysis_id=analysis_id, clip_id=a["clip_id"])
        return S.JobAccepted(job_id=job_id, analysis_id=analysis_id, state="queued")

    @app.get(f"{API}/analyses/{{analysis_id}}", response_model=S.AnalysisOut)
    def get_analysis(analysis_id: str, s: Session | None = Depends(session)) -> S.AnalysisOut:
        analysis_row(analysis_id, s)
        return views.analysis_out(svc, analysis_id)

    @app.get(f"{API}/clips/{{clip_id}}/analyses", response_model=list[S.AnalysisOut])
    def clip_analyses(clip_id: str, s: Session | None = Depends(session)) -> list[S.AnalysisOut]:
        clip_row(clip_id, s)
        return [views.analysis_out(svc, r["id"]) for r in svc.db.query(
            "SELECT id FROM analyses WHERE clip_id = ? ORDER BY created_at DESC", (clip_id,))]

    @app.get(f"{API}/analyses/{{analysis_id}}/observations", response_model=S.ObservationsPage)
    def observations(analysis_id: str, t0_ms: int, t1_ms: int, s: Session | None = Depends(session)) -> S.ObservationsPage:
        a = analysis_row(analysis_id, s)
        if t1_ms < t0_ms or t0_ms < 0:
            raise AppError("INVALID_ARGUMENT", "Choose a valid time window.", status_code=400)
        end = min(t1_ms, t0_ms + 10_000)
        clip = svc.store.clip(a["clip_id"])
        rows = svc.store.observations(analysis_id, t0_ms=t0_ms, t1_ms=end)
        return S.ObservationsPage(analysis_id=analysis_id, t0_ms=t0_ms, t1_ms=end, frame_width=clip["proxy_width"] or 0,
                                  frame_height=clip["proxy_height"] or 0, next_t0_ms=end + 1 if end < t1_ms else None,
                                  observations=[S.ObservationOut(entity_id=o.entity_id, proxy_frame_index=o.proxy_frame_index,
                                                                 t_ms=o.t_ms, box=o.box, mask_svg=o.mask_svg,
                                                                 detection_state=o.detection_state,
                                                                 confidence_bp=o.provider_confidence_bp,
                                                                 identity_warning=o.identity_warning) for o in rows])

    # Questions ---------------------------------------------------------------------------------------
    @app.post(f"{API}/analyses/{{analysis_id}}/questions", response_model=S.JobAccepted, status_code=202)
    def ask(analysis_id: str, body: S.QuestionCreate, s: Session = Depends(mutation)) -> S.JobAccepted:
        a = analysis_row(analysis_id, s)
        if a["owner_id"] != s.owner_id:
            raise AppError("DEMO_READ_ONLY", "Recorded demonstrations replay their recorded questions only.", status_code=403)
        if a["status"] not in ("completed", "partial"):
            raise AppError("ANALYSIS_NOT_READY", "Wait for the analysis to finish before asking.", status_code=409)
        if a["run_mode"] == "live":
            live_gate(s)
        inference_limit.check(f"inference:{s.owner_id}")
        payload = {"question": body.question, "object_id": body.object_id, "analysis_id": analysis_id}
        prior = svc.db.one("SELECT id, investigation_id FROM jobs WHERE owner_id = ? AND kind = 'question' AND idempotency_key = ?",
                           (s.owner_id, body.idempotency_key))
        if prior is not None:
            job_id, _ = enqueue(owner_id=s.owner_id, kind="question", key=body.idempotency_key, payload=payload, run_mode=a["run_mode"])
            return S.JobAccepted(job_id=job_id, analysis_id=analysis_id, investigation_id=prior["investigation_id"], state="queued")
        inv_id = new_investigation(svc, owner_id=s.owner_id, analysis_id=analysis_id, question=body.question,
                                   object_id=body.object_id, run_mode=a["run_mode"])
        job_id, _ = enqueue(owner_id=s.owner_id, kind="question", key=body.idempotency_key, payload=payload, run_mode=a["run_mode"],
                            analysis_id=analysis_id, investigation_id=inv_id)
        return S.JobAccepted(job_id=job_id, analysis_id=analysis_id, investigation_id=inv_id, state="queued")

    @app.get(f"{API}/analyses/{{analysis_id}}/investigations", response_model=list[S.InvestigationOut])
    def list_investigations(analysis_id: str, s: Session | None = Depends(session)) -> list[S.InvestigationOut]:
        analysis_row(analysis_id, s)
        return [views.investigation_out(svc, dict(r)) for r in svc.db.query(
            "SELECT * FROM investigations WHERE analysis_id = ? ORDER BY created_at DESC", (analysis_id,))]

    @app.get(f"{API}/investigations/{{investigation_id}}", response_model=S.InvestigationOut)
    def get_investigation(investigation_id: str, s: Session | None = Depends(session)) -> S.InvestigationOut:
        row = svc.db.one("SELECT * FROM investigations WHERE id = ?", (investigation_id,))
        if row is None or not readable(row["owner_id"], s):
            raise not_found("investigation")
        analysis_row(row["analysis_id"], s)
        return views.investigation_out(svc, dict(row))

    # Jobs and events -------------------------------------------------------------------------------------
    @app.get(f"{API}/jobs/{{job_id}}", response_model=S.JobOut)
    def get_job(job_id: str, s: Session | None = Depends(session)) -> S.JobOut:
        return views.job_out(job_row(job_id, s))

    @app.post(f"{API}/jobs/{{job_id}}/cancel", response_model=S.JobOut, status_code=202)
    def cancel_job(job_id: str, s: Session = Depends(mutation)) -> S.JobOut:
        job = job_row(job_id, s)
        if job["owner_id"] != s.owner_id:
            raise not_found("job")
        with svc.db.write() as conn:
            conn.execute("UPDATE jobs SET state = 'cancelled', finished_at = ? WHERE id = ? AND state = 'queued'", (utc_now(), job_id))
            conn.execute("UPDATE jobs SET state = 'cancel_requested' WHERE id = ? AND state = 'running'", (job_id,))
        worker.cancel(job_id)
        fresh = job_row(job_id, s)
        if fresh["state"] == "cancelled" and job["state"] == "queued":
            svc.events.append(job_id, "job.cancelled", {"summary": "Cancelled before it started."}, mode=job["run_mode"])
            if job["analysis_id"] and job["kind"] == "analysis":
                with svc.db.write() as conn:
                    conn.execute("UPDATE analyses SET status = 'cancelled' WHERE id = ? AND status = 'queued'", (job["analysis_id"],))
        return views.job_out(fresh)

    @app.get(f"{API}/jobs/{{job_id}}/events")
    async def job_events(job_id: str, request: Request, s: Session | None = Depends(session),
                         last_event_id: str | None = Header(default=None)) -> StreamingResponse:
        job_row(job_id, s)
        start = int(last_event_id) if last_event_id and last_event_id.isdigit() else int(request.query_params.get("after", "0") or 0)

        async def stream():
            seq = start
            idle = 0.0
            while True:
                if await request.is_disconnected():
                    return
                batch = await asyncio.to_thread(svc.events.after, job_id, seq)
                for event in batch:
                    seq = event["seq"]
                    yield f"id: {seq}\nevent: {event['type']}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
                if batch:
                    idle = 0.0
                    if batch[-1]["type"] in ("job.completed", "job.partial", "job.failed", "job.cancelled", "job.needs_selection"):
                        return
                    continue
                state = await asyncio.to_thread(lambda: svc.db.require("SELECT state FROM jobs WHERE id = ?", (job_id,), "job")["state"])
                if state in ("completed", "partial", "failed", "cancelled", "interrupted", "needs_selection"):
                    return  # nothing newer to send for a finished job
                await asyncio.sleep(0.25)
                idle += 0.25
                if idle >= 15:
                    idle = 0.0
                    yield ": keepalive\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # Assets -----------------------------------------------------------------------------------------------
    @app.get(f"{API}/assets/{{asset_id}}")
    def get_asset(asset_id: str, s: Session | None = Depends(session)) -> FileResponse:
        row = svc.storage.asset_row(asset_id, s.owner_id if s else None)
        clip = svc.db.one("SELECT status FROM clips WHERE id = ?", (row["clip_id"],))
        if clip is None or clip["status"] in ("deleted", "expired"):
            raise not_found("asset")
        path, media = svc.storage.asset_path(asset_id, row["owner_id"])
        headers = {"Cache-Control": "private, max-age=86400, immutable"}
        if media == "application/zip":
            headers["Content-Disposition"] = f'attachment; filename="{path.name}"'
        return FileResponse(path, media_type=media, headers=headers)

    # Exports --------------------------------------------------------------------------------------------
    def export_out(row: dict) -> S.ExportOut:
        trusted = svc.db.one("SELECT head_digest, source FROM trusted_heads WHERE export_id = ?", (row["id"],))
        return S.ExportOut(export_id=row["id"], analysis_id=row["analysis_id"], status=row["status"], head_digest=row["head_digest"],
                           trusted_head=trusted["head_digest"] if trusted else None,
                           trusted_head_source=trusted["source"] if trusted else None, file_count=row["file_count"],
                           bytes=row["bytes"], download_url=f"{API}/exports/{row['id']}/download" if row["archive_asset_id"] else None,
                           created_at=row["created_at"], sealed_at=row["sealed_at"])

    def export_row(export_id: str, s: Session | None) -> dict:
        row = svc.db.one("SELECT * FROM exports WHERE id = ?", (export_id,))
        if row is None or not readable(row["owner_id"], s):
            raise not_found("export")
        analysis_row(row["analysis_id"], s)
        return dict(row)

    @app.post(f"{API}/analyses/{{analysis_id}}/exports", response_model=S.JobAccepted, status_code=202)
    def create_export(analysis_id: str, body: S.ExportCreate, s: Session = Depends(mutation)) -> S.JobAccepted:
        a = analysis_row(analysis_id, s)
        if a["owner_id"] != s.owner_id:
            raise AppError("DEMO_READ_ONLY", "Use the published bundle for recorded demonstrations.", status_code=403)
        if a["status"] not in ("completed", "partial"):
            raise AppError("ANALYSIS_NOT_READY", "Only a finished analysis can be exported.", status_code=409)
        prior = svc.db.one("SELECT id, export_id FROM jobs WHERE owner_id = ? AND kind = 'export' AND idempotency_key = ?",
                           (s.owner_id, body.idempotency_key))
        if prior is not None:
            return S.JobAccepted(job_id=prior["id"], analysis_id=analysis_id, export_id=prior["export_id"], state="queued")
        export_id = new_id("exp")
        with svc.db.write() as conn:
            conn.execute("INSERT INTO exports(id, owner_id, analysis_id, status, created_at) VALUES (?, ?, ?, 'queued', ?)",
                         (export_id, s.owner_id, analysis_id, utc_now()))
        job_id, _ = enqueue(owner_id=s.owner_id, kind="export", key=body.idempotency_key, payload={"analysis_id": analysis_id},
                            run_mode=a["run_mode"], analysis_id=analysis_id, export_id=export_id)
        return S.JobAccepted(job_id=job_id, analysis_id=analysis_id, export_id=export_id, state="queued")

    @app.get(f"{API}/analyses/{{analysis_id}}/exports", response_model=list[S.ExportOut])
    def list_exports(analysis_id: str, s: Session | None = Depends(session)) -> list[S.ExportOut]:
        analysis_row(analysis_id, s)
        return [export_out(dict(r)) for r in svc.db.query("SELECT * FROM exports WHERE analysis_id = ? ORDER BY created_at DESC",
                                                          (analysis_id,))]

    @app.get(f"{API}/exports/{{export_id}}", response_model=S.ExportOut)
    def get_export(export_id: str, s: Session | None = Depends(session)) -> S.ExportOut:
        return export_out(export_row(export_id, s))

    @app.get(f"{API}/exports/{{export_id}}/download")
    def download_export(export_id: str, s: Session | None = Depends(session)) -> FileResponse:
        row = export_row(export_id, s)
        if not row["archive_asset_id"]:
            raise not_found("export")
        path, media = svc.storage.asset_path(row["archive_asset_id"], row["owner_id"])
        return FileResponse(path, media_type=media, filename=f"scenetrace-{export_id}.zip")

    @app.post(f"{API}/exports/{{export_id}}/verify", response_model=S.VerifyOut)
    def verify(export_id: str, body: S.VerifyIn, s: Session | None = Depends(session)) -> S.VerifyOut:
        row = export_row(export_id, s)
        if row["status"] != "sealed":
            raise AppError("EXPORT_NOT_READY", "The export is not sealed yet.", status_code=409)
        result = verify_export(svc, row, body.expected_head)
        note = ("Integrity checks the files against their sealed hashes. It does not prove the video is genuine or that any"
                " interpretation is correct.")
        return S.VerifyOut(export_id=export_id, integrity=result["integrity"], anchored=result["anchored"],
                           anchor_source=result["anchor_source"], head=result["head"], errors=result["errors"], note=note)

    # Built client ------------------------------------------------------------------------------------------
    if (CLIENT_DIST / "index.html").is_file():
        @app.get("/{path:path}", include_in_schema=False)
        def client(path: str) -> Response:
            if path.startswith("api/"):
                raise not_found()
            candidate = (CLIENT_DIST / path).resolve()
            if path and candidate.is_file() and CLIENT_DIST.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(CLIENT_DIST / "index.html", headers={"Cache-Control": "no-cache"})

    return app


def _commit(root: Path) -> str | None:
    head = root / ".git" / "HEAD"
    try:
        ref = head.read_text().strip()
        if ref.startswith("ref: "):
            return (root / ".git" / ref[5:]).read_text().strip()[:12]
        return ref[:12]
    except OSError:
        return None
