"""Job handlers: normalisation, the cold analysis and warm questions.

Cold: validating -> resolving_target -> tracking -> extracting_candidates ->
collecting_evidence -> assessing -> compiling. Warm: investigating (bounded
controller) -> compiled answer. Every stage checks cancellation and the deadline
before scheduling new work, and partial results are kept with explicit reasons.
"""
from __future__ import annotations

import hashlib
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from PIL import Image

from ..db import dumps, loads
from ..domain import BoxPrompt, ConceptPrompt, ProxyVideo, Zone
from ..engine.candidates import generate_candidates
from ..engine.config import DEFAULT_ENGINE_CONFIG
from ..engine.evidence import collect_evidence, raw_evidence_ids
from ..engine.observations import build_observations
from ..engine.promotion import promote
from ..errors import AppError
from ..ids import new_id, utc_now
from ..investigator.assess import assess_candidate
from ..investigator.controller import run_investigation
from ..media.normalise import normalise_clip, proxy_frame_png
from ..media.probe import probe_media
from ..providers.base import CancelToken, ProviderSet, RunContext
from ..providers.factory import build_providers
from ..providers.meta_sam import PARSER_VERSION
from .context import Services


class NeedsSelection(Exception):
    pass


def _update(svc: Services, table: str, row_id: str, **fields: Any) -> None:
    cols = ", ".join(f"{k} = ?" for k in fields)
    with svc.db.write() as conn:
        conn.execute(f"UPDATE {table} SET {cols} WHERE id = ?", (*fields.values(), row_id))


def source_path(svc: Services, clip: dict) -> Path:
    return svc.storage.source_dir(clip["owner_id"], clip["id"]) / "source.media"


def proxy_video(svc: Services, clip: dict) -> ProxyVideo:
    return ProxyVideo(clip_id=clip["id"], owner_id=clip["owner_id"],
                      path=str(svc.storage.proxy_dir(clip["owner_id"], clip["id"]) / "proxy.mp4"),
                      sha256=clip["proxy_sha256"], width=clip["proxy_width"], height=clip["proxy_height"], fps=clip["proxy_fps"],
                      frame_count=clip["proxy_frame_count"], frame_map_sha256=clip["frame_map_sha256"])


# Normalisation ----------------------------------------------------------------------
def run_normalise(svc: Services, job: dict, cancel: CancelToken) -> str:
    clip = svc.store.clip(job["clip_id"])
    emit = svc.events.emitter(job["id"], None)
    started = time.monotonic()
    emit("stage.started", {"stage": "validating", "summary": "Checking the uploaded file."})
    _update(svc, "clips", clip["id"], status="normalising")
    source = source_path(svc, clip)
    try:
        probe = probe_media(source, settings=svc.settings, cancel=cancel)
        emit("stage.started", {"stage": "normalising", "summary": "Creating the 10 fps tracking proxy and frame map."})
        result = normalise_clip(source, svc.storage.proxy_dir(clip["owner_id"], clip["id"]), probe=probe,
                                settings=svc.settings, cancel=cancel)
    except AppError as exc:
        _update(svc, "clips", clip["id"], status="rejected", reject_code=exc.code)
        raise
    svc.storage.register_asset(owner_id=clip["owner_id"], clip_id=clip["id"], path=result.proxy_path, kind="proxy",
                               media_type="video/mp4", derived=True, public=clip["owner_id"] == "public")
    manifest = {"ffmpeg_version": result.ffmpeg_version, "command": result.command, "container": probe.container,
                "codec": probe.codec, "variable_frame_rate": probe.variable_frame_rate, "source_frames": probe.frame_count}
    _update(svc, "clips", clip["id"], status="ready", source_container=probe.container, source_codec=probe.codec,
            source_width=probe.width, source_height=probe.height, rotation_deg=probe.rotation_deg,
            source_fps_num=probe.fps_num, source_fps_den=probe.fps_den, duration_ms=probe.duration_ms,
            proxy_sha256=result.proxy_sha256, proxy_width=result.width, proxy_height=result.height, proxy_fps=result.fps,
            proxy_frame_count=result.frame_count, frame_map_sha256=result.frame_map_sha256,
            preprocessing_version=result.preprocessing_version, manifest_json=dumps(manifest),
            normalise_ms=int((time.monotonic() - started) * 1000))
    emit("stage.completed", {"stage": "normalising", "summary": f"Proxy ready: {result.frame_count} frames at {result.fps} fps."})
    return "completed"


# Cold analysis --------------------------------------------------------------------------------
def _recording_dir(svc: Services, analysis: dict, clip: dict) -> Path:
    source_analysis = analysis["recorded_from"] or analysis["id"]
    return svc.storage.analysis_dir(clip["owner_id"], clip["id"], source_analysis) / "recording"


def _context(svc: Services, job: dict, analysis: dict, cancel: CancelToken, *, scope: str,
             investigation_id: str | None = None) -> RunContext:
    deadline_ms = svc.settings.limit("cold_job_deadline_ms" if scope == "cold" else "warm_question_deadline_ms")
    return RunContext(owner_id=analysis["owner_id"], scope=scope, run_mode=analysis["run_mode"], ledger=svc.ledger,  # type: ignore[arg-type]
                      analysis_id=analysis["id"], investigation_id=investigation_id, job_id=job["id"],
                      deadline_monotonic=time.monotonic() + deadline_ms / 1000, cancel=cancel,
                      emit=svc.events.emitter(job["id"], analysis["run_mode"]))


def _cache_key(owner: str, run_mode: str, video: ProxyVideo, model_id: str, role: str, prompt: Any, preprocessing: str) -> str:
    material = {"owner": owner, "mode": run_mode, "proxy": video.sha256, "model": model_id, "role": role,
                "prompt": prompt.model_dump(), "frames": [0, video.frame_count - 1], "parser": PARSER_VERSION,
                "preprocessing": preprocessing}
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def _resolve_target(svc: Services, ctx: RunContext, providers: ProviderSet, analysis: dict, clip: dict,
                    video: ProxyVideo, adir: Path) -> tuple[int, list[int]]:
    target = loads(analysis["target_json"])
    if target["kind"] == "box":
        return int(target["frame_index"]), list(target["xyxy_exclusive"])
    if analysis["selected_selection_id"]:
        row = svc.db.one("SELECT * FROM selections WHERE id = ? AND analysis_id = ?", (analysis["selected_selection_id"], analysis["id"]))
        return int(row["proxy_frame_index"]), [row["x0"], row["y0"], row["x1"], row["y1"]]
    frame = min(int(target.get("frame_index", 0)), video.frame_count - 1)
    png = proxy_frame_png(Path(video.path), frame, fps=video.fps, cancel=ctx.cancel)
    found = providers.tracker.select_instances(ctx, image_png=png, width=video.width, height=video.height,
                                               phrase=target["phrase"], proxy_frame_index=frame)
    image = Image.open(io.BytesIO(png)).convert("RGB")
    (adir / "selection").mkdir(parents=True, exist_ok=True)
    with svc.db.write() as conn:
        conn.execute("DELETE FROM selections WHERE analysis_id = ?", (analysis["id"],))
    for cand in found:
        x0, y0, x1, y1 = cand.box
        pad = max(12, (x1 - x0) // 2)
        thumb = image.crop((max(0, x0 - pad), max(0, y0 - pad), min(image.width, x1 + pad), min(image.height, y1 + pad)))
        path = adir / "selection" / f"{cand.selection_id}.png"
        thumb.save(path)
        asset = svc.storage.register_asset(owner_id=clip["owner_id"], clip_id=clip["id"], analysis_id=analysis["id"], path=path,
                                           kind="selection_thumb", media_type="image/png", derived=True,
                                           public=clip["owner_id"] == "public")
        with svc.db.write() as conn:
            conn.execute("INSERT INTO selections(id, analysis_id, proxy_frame_index, x0, y0, x1, y1, provider_object_id,"
                         " provider_confidence_bp, thumbnail_asset_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (cand.selection_id, analysis["id"], frame, x0, y0, x1, y1, cand.provider_object_id,
                          cand.confidence_bp, asset, utc_now()))
    if len(found) == 1:
        _update(svc, "analyses", analysis["id"], selected_selection_id=found[0].selection_id)
        return frame, list(found[0].box)
    reason = "TARGET_AMBIGUOUS" if found else "TARGET_NOT_FOUND"
    _update(svc, "analyses", analysis["id"], status="needs_selection", partial_reasons_json=dumps([reason]))
    raise NeedsSelection(reason)


def run_analysis(svc: Services, job: dict, cancel: CancelToken) -> str:
    analysis = svc.store.analysis(job["analysis_id"])
    clip = svc.store.clip(analysis["clip_id"])
    ctx = _context(svc, job, analysis, cancel, scope="cold")
    adir = svc.storage.analysis_dir(clip["owner_id"], clip["id"], analysis["id"])
    adir.mkdir(parents=True, exist_ok=True)
    providers = build_providers(svc.settings, svc.db, analysis["run_mode"], recording_dir=_recording_dir(svc, analysis, clip))
    timings: dict[str, int] = {"upload_ms": clip["upload_ms"] or 0, "normalise_ms": clip["normalise_ms"] or 0}
    reasons: list[str] = []
    started = time.monotonic()
    _update(svc, "analyses", analysis["id"], status="running")
    video = proxy_video(svc, clip)
    emit = ctx.emit
    emit("stage.started", {"stage": "resolving_target", "summary": "Resolving the selected object."})
    try:
        frame, box = _resolve_target(svc, ctx, providers, analysis, clip, video, adir)
    except NeedsSelection as exc:
        emit("job.needs_selection", {"stage": "resolving_target", "summary": "Select the intended object before continuing."
                                     if str(exc) == "TARGET_AMBIGUOUS" else "No match on that frame. Choose another frame or draw a box."})
        return "needs_selection"
    emit("stage.started", {"stage": "tracking", "summary": "Tracking the selected object and people through the clip."})
    t0 = time.monotonic()
    prompts = [("target", BoxPrompt(frame_index=frame, box=box)), ("person", ConceptPrompt(phrase="person"))]

    def track(item: tuple[str, Any]):
        role, prompt = item
        key = _cache_key(clip["owner_id"], analysis["run_mode"], video, providers.tracker.model_id, role, prompt,
                         clip["preprocessing_version"] or "")
        cached = svc.store.cached_tracking(clip["owner_id"], key)
        if cached is not None:
            return cached, key, True
        return providers.tracker.track(ctx, video=video, prompt=prompt, role=role), key, False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(track, prompts))
    for result, key, cached in results:
        svc.store.save_tracking(analysis["id"], clip["owner_id"], result, key, None)
        if result.coverage.terminal_status != "completed":
            reasons.append("TRACKING_INCOMPLETE")
        emit("stage.progress", {"stage": "tracking", "summary": f"{result.role.capitalize()} tracking {'reused' if cached else 'finished'}:"
                                f" {len({r.provider_object_id for r in result.records})} track(s), {result.coverage.terminal_status}."})
    timings["tracking_ms"] = int((time.monotonic() - t0) * 1000)
    target = loads(analysis["target_json"])
    frame_map = svc.store.frame_map(clip["id"])
    observations, entities, coverage = build_observations(analysis_id=analysis["id"], clip_id=clip["id"], frame_map=frame_map,
                                                          results=[r for r, _k, _c in results])
    label = target.get("phrase") or target.get("label") or "selected object"
    entities = [e.model_copy(update={"label": label}) if e.entity_id == "O1" else e for e in entities]
    svc.store.save_observations(analysis["id"], observations, entities, coverage)
    zones = [Zone(zone_id=f"Z{i}", label=z["label"], polygon=[tuple(p) for p in z["polygon"]])
             for i, z in enumerate(loads(analysis["zones_json"], []), start=1)]
    svc.store.save_zones(analysis["id"], zones)
    emit("stage.completed", {"stage": "tracking", "summary": f"Tracks ready: {len(entities)} clip-local entities."})
    ctx.checkpoint()
    emit("stage.started", {"stage": "extracting_candidates", "summary": "Looking for changes in the object's motion or support."})
    report: dict[str, Any] = {}
    candidates = generate_candidates(observations=observations, entities=entities, coverage=coverage, zones=zones,
                                     frame_map=frame_map, config=DEFAULT_ENGINE_CONFIG, report=report)
    if report.get("capped"):
        reasons.append("CANDIDATES_CAPPED")
    emit("stage.completed", {"stage": "extracting_candidates", "summary": f"{len(candidates)} candidate(s), including sightings."})
    emit("stage.started", {"stage": "collecting_evidence", "summary": "Extracting original frames for each candidate."})
    source = source_path(svc, clip)
    for i, cand in enumerate(candidates):
        ctx.checkpoint()
        items = collect_evidence(analysis_id=analysis["id"], owner_id=clip["owner_id"], clip_id=clip["id"], candidate=cand,
                                 frame_map=frame_map, source_path=source, source_sha256=clip["source_sha256"],
                                 observations=observations, storage=svc.storage, analysis_dir=adir,
                                 first_evidence_number=svc.store.next_evidence_number(analysis["id"]), cancel=cancel)
        svc.store.add_evidence(analysis["id"], items)
        candidates[i] = cand.model_copy(update={"evidence_ids": raw_evidence_ids(items)})
        emit("evidence.created", {"stage": "collecting_evidence", "candidate_id": cand.candidate_id, "count": len(raw_evidence_ids(items))})
    svc.store.save_candidates(analysis["id"], candidates)
    emit("stage.started", {"stage": "assessing", "summary": "Structured visual assessment of interaction candidates."})
    t1 = time.monotonic()
    assessments = {}
    for cand in candidates:
        if cand.candidate_type in ("first_seen", "last_seen"):
            continue
        record = assess_candidate(ctx, providers=providers, store=svc.store, storage=svc.storage, settings=svc.settings,
                                  analysis_id=analysis["id"], owner_id=clip["owner_id"], candidate=cand)
        assessments[cand.candidate_id] = record
        if record.status != "completed":
            reasons.append("ASSESSMENT_UNAVAILABLE")
        emit("stage.progress", {"stage": "assessing", "candidate_id": cand.candidate_id,
                                "summary": f"{cand.candidate_id} assessed: {record.result.verdict if record.result else record.status}."})
    timings["assessment_ms"] = int((time.monotonic() - t1) * 1000)
    emit("stage.started", {"stage": "compiling", "summary": "Applying the event promotion rules."})
    for number, cand in enumerate(candidates, start=1):
        record = assessments.get(cand.candidate_id)
        event, basis = promote(candidate=cand, assessment=record.result if record else None, observations=observations,
                               coverage=coverage, event_id=f"E{number}", revision=1, evidence_ids=cand.evidence_ids)
        svc.store.append_event_revision(analysis["id"], event, candidate_id=cand.candidate_id,
                                        basis={**basis, "assessment_id": record.assessment_id if record else None})
    snapshot = svc.store.snapshot(analysis["id"])
    timings["total_ms"] = timings["upload_ms"] + timings["normalise_ms"] + int((time.monotonic() - started) * 1000)
    status = "partial" if reasons else "completed"
    _update(svc, "analyses", analysis["id"], status=status, head_digest=snapshot["digest"], timings_json=dumps(timings),
            partial_reasons_json=dumps(sorted(set(reasons))), completed_at=utc_now(), parser_version=PARSER_VERSION,
            preprocessing_version=clip["preprocessing_version"], rate_card_revision=svc.settings.rates.revision)
    emit("stage.completed", {"stage": "compiling", "summary": f"History ready: {len(candidates)} event record(s)."})
    return status


# Warm questions --------------------------------------------------------------------------------
def _call_total(rows: list[dict]) -> tuple[int, str]:
    total, statuses = 0, set()
    for r in rows:
        if r["state"] in ("released",):
            continue
        amount = r["actual_microusd"] if r["actual_microusd"] is not None else r["reserved_microusd"]
        total += amount or 0
        statuses.add(r["cost_status"])
    if not rows or statuses <= {"free", "released"}:
        return total, "none"
    if "unknown" in statuses:
        return total, "unknown"
    if statuses & {"replayed"}:
        return total, "replayed"
    return total, "provider_usage_based" if statuses <= {"provider_usage_based", "free"} else "estimated"


def original_cost(svc: Services, analysis: dict) -> tuple[int, str]:
    source = analysis["recorded_from"] or analysis["id"]
    return _call_total(svc.ledger.calls_for(analysis_id=source))


def run_question(svc: Services, job: dict, cancel: CancelToken) -> str:
    inv = dict(svc.db.one("SELECT * FROM investigations WHERE id = ?", (job["investigation_id"],)))
    analysis = svc.store.analysis(inv["analysis_id"])
    clip = svc.store.clip(analysis["clip_id"])
    ctx = _context(svc, job, analysis, cancel, scope="warm", investigation_id=inv["id"])
    started = time.monotonic()
    _update(svc, "investigations", inv["id"], status="running")
    ctx.emit("stage.started", {"stage": "investigating", "summary": "Reading the stored history before any new inspection."})
    providers = build_providers(svc.settings, svc.db, analysis["run_mode"], recording_dir=_recording_dir(svc, analysis, clip))
    outcome = run_investigation(ctx, providers=providers, store=svc.store, storage=svc.storage, settings=svc.settings,
                                analysis_id=analysis["id"], owner_id=analysis["owner_id"], question=inv["question"],
                                object_id=inv["object_id"])
    ctx.emit("stage.completed", {"stage": "compiling", "summary": f"Answer compiled from {len(outcome.claims)} validated claim(s)."})
    question_cost, cost_status = _call_total(svc.ledger.calls_for(investigation_id=inv["id"]))
    original, _ = original_cost(svc, analysis)
    result = {"sentences": outcome.sentences, "claims": [c.model_dump() for c in outcome.claims],
              "rejected_claims": outcome.rejected_claims, "unknown_codes": outcome.unknown_codes,
              "answer_source": outcome.answer_source, "review_findings": outcome.review_findings,
              "original_processing_cost_microusd": original, "current_request_cost_microusd": question_cost,
              "cost_status": cost_status, "plan_source": outcome.plan_source}
    _update(svc, "investigations", inv["id"], status=outcome.status, plan_json=dumps({"intent": outcome.intent}),
            snapshot_json=dumps(outcome.snapshot), result_json=dumps(result), review_mode=outcome.review_mode,
            review_verdict=outcome.review_verdict, limits_json=dumps(outcome.limits_used),
            timings_json=dumps({"question_ms": int((time.monotonic() - started) * 1000)}), completed_at=utc_now())
    return outcome.status


def new_investigation(svc: Services, *, owner_id: str, analysis_id: str, question: str, object_id: str,
                      run_mode: str) -> str:
    inv_id = new_id("inv")
    with svc.db.write() as conn:
        conn.execute("INSERT INTO investigations(id, owner_id, analysis_id, question, object_id, run_mode, status, created_at)"
                     " VALUES (?, ?, ?, ?, ?, ?, 'queued', ?)", (inv_id, owner_id, analysis_id, question, object_id, run_mode, utc_now()))
    return inv_id
