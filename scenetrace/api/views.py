"""Assemble API DTOs from stored records. URLs are server routes by ID, never filesystem paths."""
from __future__ import annotations

from typing import Any, cast

from ..db import loads
from ..engine.promotion import STATUS_MEANING
from ..jobs.context import Services
from ..jobs.pipeline import _call_total, original_cost
from . import schemas as S

API = "/api/v1"


def asset_url(asset_id: str | None) -> str | None:
    return f"{API}/assets/{asset_id}" if asset_id else None


def clip_out(svc: Services, row: dict) -> S.ClipOut:
    analyses = [r["id"] for r in svc.db.query("SELECT id FROM analyses WHERE clip_id = ? ORDER BY created_at", (row["id"],))]
    job = svc.db.one("SELECT id FROM jobs WHERE clip_id = ? AND kind = 'normalise' ORDER BY created_at DESC LIMIT 1", (row["id"],))
    return S.ClipOut(clip_id=row["id"], status=row["status"], media_kind=row["media_kind"], display_name=row["display_name"],
                     source_sha256=row["source_sha256"], source_bytes=row["source_bytes"], duration_ms=row["duration_ms"],
                     source_width=row["source_width"], source_height=row["source_height"], rotation_deg=row["rotation_deg"],
                     proxy_width=row["proxy_width"], proxy_height=row["proxy_height"], proxy_fps=row["proxy_fps"],
                     proxy_frame_count=row["proxy_frame_count"],
                     video_url=f"{API}/clips/{row['id']}/video" if row["status"] == "ready" else None,
                     reject_code=row["reject_code"], normalise_job_id=job["id"] if job else None, analyses=analyses,
                     created_at=row["created_at"], expires_at=row["expires_at"], demo=row["owner_id"] == "public")


def usage_out(rows: list[dict], revision: str | None) -> S.UsageOut:
    total, status = _call_total(rows)
    calls = [S.ProviderCallOut(provider_call_id=r["id"], operation=r["operation"], model_id=r["model_id"], attempt=r["attempt"],
                               state=r["state"], cost_status=r["cost_status"], reserved_microusd=r["reserved_microusd"],
                               actual_microusd=r["actual_microusd"], elapsed_ms=r["elapsed_ms"], request_id=r["request_id"],
                               error_code=r["error_code"], created_at=r["created_at"]) for r in rows]
    return S.UsageOut(total_microusd=total, cost_status=cast(Any, status), provider_calls=sum(1 for r in rows if r["cost_status"] != "free"),
                      unknown_cost_calls=sum(1 for r in rows if r["cost_status"] == "unknown"), calls=calls,
                      rate_card_revision=revision)


def analysis_out(svc: Services, analysis_id: str) -> S.AnalysisOut:
    store = svc.store
    a = store.analysis(analysis_id)
    clip = store.clip(a["clip_id"])
    fps = clip["proxy_fps"] or 10
    current = store.current_events(analysis_id)
    candidates = store.candidates(analysis_id)
    by_candidate = {rev.candidate_id: rev for rev in current.values()}
    events = []
    for event_id, rev in current.items():
        history = [S.EventRevisionOut(record=h.record, record_sha256=h.record_sha256, created_at=h.created_at, basis=h.basis)
                   for h in store.event_history(analysis_id, event_id)]
        events.append(S.EventOut(current=history[-1], history=history, candidate_id=rev.candidate_id,
                                 status_meaning=STATUS_MEANING[rev.record.status]))
    cand_out = []
    for c in candidates:
        rev = by_candidate.get(c.candidate_id)
        cand_out.append(S.CandidateOut(candidate_id=c.candidate_id, candidate_type=c.candidate_type, t_start_ms=c.t_start_ms,
                                       t_end_ms=c.t_end_ms, window_start_ms=c.window_start_ms, window_end_ms=c.window_end_ms,
                                       actor_candidates=c.actor_candidates, zone_id=c.zone_id, reason_codes=c.reason_codes,
                                       evidence_ids=c.evidence_ids, cues=c.cues, event_id=rev.record.event_id if rev else None,
                                       outcome=rev.record.status if rev else "pending"))
    evidence = [S.EvidenceOut(evidence_id=i.evidence.evidence_id, candidate_id=i.candidate_id, role=i.evidence.role, kind=i.kind,
                              t_ms=i.evidence.t_ms, proxy_frame_index=i.proxy_frame_index,
                              source_frame_index=i.source_frame_index, source_pts_ms=i.source_pts_ms, derived=i.evidence.derived,
                              asset_sha256=i.evidence.asset_sha256, image_url=asset_url(i.asset_id) or "", width=i.width,
                              height=i.height) for i in store.evidence(analysis_id).values()]
    selections = [S.SelectionOut(selection_id=r["id"], proxy_frame_index=r["proxy_frame_index"],
                                 t_ms=round(r["proxy_frame_index"] * 1000 / fps), box=[r["x0"], r["y0"], r["x1"], r["y1"]],
                                 thumbnail_url=asset_url(r["thumbnail_asset_id"]), confidence_bp=r["provider_confidence_bp"])
                  for r in svc.db.query("SELECT * FROM selections WHERE analysis_id = ? ORDER BY x0", (analysis_id,))]
    zones = [S.EntityOut(entity_id=z.zone_id, kind="zone", label=z.label, first_t_ms=None, last_t_ms=None,
                         identity_warning=False, polygon=[list(p) for p in z.polygon]) for z in store.zones(analysis_id)]
    entities = [S.EntityOut(entity_id=e.entity_id, kind=e.kind, label=e.label, first_t_ms=e.first_t_ms, last_t_ms=e.last_t_ms,
                            identity_warning=e.identity_warning) for e in store.entities(analysis_id)]
    coverage = [S.CoverageOut(request_key=c.request_key, role=c.role, concept=c.concept,
                              requested_start_ms=round(c.requested_start_frame * 1000 / fps),
                              requested_end_ms=round(c.requested_end_frame * 1000 / fps), terminal_status=c.terminal_status,
                              missing_ranges_ms=[[round(x * 1000 / fps), round(y * 1000 / fps)] for x, y in c.missing_ranges])
                for c in store.coverage(analysis_id)]
    assessments = [S.AssessmentOut(assessment_id=r.assessment_id, candidate_id=r.candidate_id, purpose=r.purpose,
                                   model_id=r.model_id, run_mode=r.run_mode, status=r.status,
                                   result=r.result.model_dump() if r.result else None, created_at=r.created_at)
                   for r in store.assessments(analysis_id)]
    source = a["recorded_from"] or analysis_id
    rows = svc.ledger.calls_for(analysis_id=source)
    job = svc.db.one("SELECT id FROM jobs WHERE analysis_id = ? AND kind = 'analysis' ORDER BY created_at DESC LIMIT 1", (analysis_id,))
    return S.AnalysisOut(
        analysis_id=analysis_id, clip_id=clip["id"], job_id=job["id"] if job else None, status=a["status"], run_mode=a["run_mode"],
        media_kind=clip["media_kind"], source_sha256=clip["source_sha256"], target=loads(a["target_json"], {}), zones=zones,
        selections=selections, entities=entities, events=events, candidates=cand_out, evidence=evidence,
        assessments=assessments, coverage=coverage,
        provenance=S.ProvenanceOut(run_mode=a["run_mode"], models=loads(a["models_json"], {}), parser_version=a["parser_version"],
                                   preprocessing_version=a["preprocessing_version"], recorded_at=a["recorded_at"],
                                   recorded_from=a["recorded_from"]),
        usage=usage_out(rows, a["rate_card_revision"]), timings_ms=loads(a["timings_json"], {}),
        partial_reasons=loads(a["partial_reasons_json"], []), created_at=a["created_at"], completed_at=a["completed_at"],
        demo=a["owner_id"] == "public")


def investigation_out(svc: Services, row: dict) -> S.InvestigationOut:
    result: dict[str, Any] = loads(row["result_json"], {}) or {}
    analysis = svc.store.analysis(row["analysis_id"])
    original, _ = original_cost(svc, analysis)
    job = svc.db.one("SELECT id FROM jobs WHERE investigation_id = ? ORDER BY created_at DESC LIMIT 1", (row["id"],))
    return S.InvestigationOut(
        investigation_id=row["id"], analysis_id=row["analysis_id"], job_id=job["id"] if job else None, question=row["question"],
        object_id=row["object_id"], status=row["status"], run_mode=row["run_mode"],
        intent=(loads(row["plan_json"], {}) or {}).get("intent"),
        sentences=[S.AnswerSentenceOut(**s) for s in result.get("sentences", [])],
        claims=result.get("claims", []), rejected_claims=[S.RejectedClaimOut(**r) for r in result.get("rejected_claims", [])],
        unknown_codes=result.get("unknown_codes", []), answer_source=result.get("answer_source", "pending"),
        review_mode=row["review_mode"], review_verdict=row["review_verdict"],
        review_findings=[S.ReviewFindingOut(**f) for f in result.get("review_findings", [])],
        snapshot=loads(row["snapshot_json"], {}), limits_used=loads(row["limits_json"], {}),
        original_processing_cost_microusd=result.get("original_processing_cost_microusd", original),
        current_request_cost_microusd=result.get("current_request_cost_microusd", 0),
        cost_status=result.get("cost_status", "none"), timings_ms=loads(row["timings_json"], {}), created_at=row["created_at"],
        completed_at=row["completed_at"], recorded_at=row["recorded_at"])


def job_out(row: dict) -> S.JobOut:
    error = None
    if row["error_code"]:
        error = S.ErrorBody(code=row["error_code"], message=row["error_message"] or "", retryable=False, request_id=row["id"])
    return S.JobOut(job_id=row["id"], kind=row["kind"], state=row["state"], stage=row["stage"], run_mode=row["run_mode"],
                    clip_id=row["clip_id"], analysis_id=row["analysis_id"], investigation_id=row["investigation_id"],
                    export_id=row["export_id"], error=error, partial_outcome=loads(row["partial_outcome_json"]),
                    created_at=row["created_at"], started_at=row["started_at"], finished_at=row["finished_at"])
