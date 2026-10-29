"""The five investigator tools. Arguments are validated locally against the published
schemas and server-known IDs; the server chooses every path, prompt and frame budget.
Results are compact JSON of stored records, never model prose."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator

from ..config import ROOT, Settings
from ..domain import Candidate
from ..engine.evidence import collect_evidence, raw_evidence_ids
from ..engine.promotion import promote
from ..providers.base import ProviderSet, RunContext
from ..storage import Storage
from ..store import AnalysisStore
from .assess import assess_candidate

TOOLS: list[dict[str, Any]] = json.loads((ROOT / "contracts" / "investigator-tools.responses.json").read_text(encoding="utf-8"))
VALIDATORS = {t["name"]: Draft202012Validator(t["parameters"]) for t in TOOLS}


@dataclass
class ToolContext:
    ctx: RunContext
    providers: ProviderSet
    store: AnalysisStore
    storage: Storage
    settings: Settings
    analysis_id: str
    owner_id: str
    object_id: str
    allowed_unknown: set[str]
    limits: dict[str, int] = field(default_factory=lambda: {"inspections": 0})
    events_used: set[str] = field(default_factory=set)


def _event_view(revision: Any) -> dict[str, Any]:
    r = revision.record
    return {"event_id": r.event_id, "revision": r.revision, "object_id": r.object_id, "event_type": r.event_type,
            "status": r.status, "actor_id": r.actor_id, "zone_id": r.zone_id, "t_start_ms": r.t_start_ms,
            "t_end_ms": r.t_end_ms, "reason_codes": r.reason_codes, "candidate_id": revision.candidate_id}


def _candidate_view(c: Candidate, outcome: str | None) -> dict[str, Any]:
    return {"candidate_id": c.candidate_id, "candidate_type": c.candidate_type, "t_start_ms": c.t_start_ms,
            "t_end_ms": c.t_end_ms, "actor_candidates": c.actor_candidates, "reason_codes": c.reason_codes, "outcome": outcome}


def _events(tc: ToolContext) -> dict[str, Any]:
    return tc.store.current_events(tc.analysis_id)


def read_history(tc: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    events = _events(tc)
    outcome = {v.candidate_id: v.record.status for v in events.values()}
    coverage = tc.store.coverage(tc.analysis_id)
    return {"object_id": tc.object_id,
            "events": [_event_view(v) for v in events.values()],
            "candidates": [_candidate_view(c, outcome.get(c.candidate_id)) for c in tc.store.candidates(tc.analysis_id)],
            "coverage": [{"role": c.role, "terminal_status": c.terminal_status} for c in coverage],
            "allowed_unknown_codes": sorted(tc.allowed_unknown)}


def read_events(tc: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    t0, t1 = args["t_start_ms"], args["t_end_ms"]
    if t1 < t0:
        return {"error": "INVALID_INTERVAL"}
    events = [_event_view(v) for v in _events(tc).values() if v.record.t_end_ms >= t0 and v.record.t_start_ms <= t1]
    return {"object_id": tc.object_id, "t_start_ms": t0, "t_end_ms": t1, "events": events}


def _event_for(tc: ToolContext, candidate_id: str) -> Any:
    return next((v for v in _events(tc).values() if v.candidate_id == candidate_id), None)


def _reassess(tc: ToolContext, candidate: Candidate, purpose: str, *, new_evidence: bool) -> dict[str, Any]:
    if tc.limits["inspections"] >= tc.settings.limit("max_extra_inspections"):
        return {"error": "INSPECTION_LIMIT_REACHED"}
    tc.limits["inspections"] += 1
    analysis = tc.store.analysis(tc.analysis_id)
    clip = tc.store.clip(analysis["clip_id"])
    if new_evidence:
        frame_map = tc.store.frame_map(clip["id"])
        tighter = candidate.model_copy(update={"window_start_ms": max(0, candidate.t_start_ms - 750),
                                               "window_end_ms": min(frame_map.entries[-1].t_ms, candidate.t_end_ms + 750)})
        source = next(tc.storage.source_dir(clip["owner_id"], clip["id"]).glob("source.*"))
        items = collect_evidence(analysis_id=tc.analysis_id, owner_id=clip["owner_id"], clip_id=clip["id"], candidate=tighter,
                                 frame_map=frame_map, source_path=source, source_sha256=clip["source_sha256"],
                                 observations=tc.store.observations(tc.analysis_id), storage=tc.storage,
                                 analysis_dir=tc.storage.analysis_dir(clip["owner_id"], clip["id"], tc.analysis_id),
                                 first_evidence_number=tc.store.next_evidence_number(tc.analysis_id), purpose=purpose,
                                 cancel=tc.ctx.cancel)
        tc.store.add_evidence(tc.analysis_id, items)
        tc.ctx.emit("evidence.created", {"candidate_id": candidate.candidate_id, "count": len(raw_evidence_ids(items))})
    record = assess_candidate(tc.ctx, providers=tc.providers, store=tc.store, storage=tc.storage, settings=tc.settings,
                              analysis_id=tc.analysis_id, owner_id=tc.owner_id, candidate=candidate, purpose=purpose)
    current = _event_for(tc, candidate.candidate_id)
    revised = None
    if current is not None and record.result is not None:
        evidence_ids = sorted(set(current.record.evidence_ids) | set(record.result.evidence_ids), key=lambda v: int(v[1:]))
        proposal, basis = promote(candidate=candidate, assessment=record.result, observations=tc.store.observations(tc.analysis_id),
                                  coverage=tc.store.coverage(tc.analysis_id), event_id=current.record.event_id,
                                  revision=current.record.revision + 1, evidence_ids=evidence_ids)
        if (proposal.status, proposal.actor_id) != (current.record.status, current.record.actor_id):
            proposal = proposal.model_copy(update={"reason_codes": sorted(set(proposal.reason_codes) | {"REVISED_BY_INSPECTION"})})
            revised = tc.store.append_event_revision(tc.analysis_id, proposal, candidate_id=candidate.candidate_id,
                                                     basis={**basis, "assessment_id": record.assessment_id, "purpose": purpose})
    latest = _event_for(tc, candidate.candidate_id)
    return {"candidate_id": candidate.candidate_id, "purpose": purpose, "assessment_status": record.status,
            "verdict": record.result.verdict if record.result else None,
            "reason_codes": record.result.reason_codes if record.result else [],
            "event": _event_view(latest) if latest else None, "revised": revised is not None}


def inspect_window(tc: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    candidate = tc.store.candidate(tc.analysis_id, args["candidate_id"])
    if candidate is None:
        return {"error": "UNKNOWN_CANDIDATE"}
    return _reassess(tc, candidate, args["purpose"], new_evidence=True)


def assess_event(tc: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    candidate = tc.store.candidate(tc.analysis_id, args["candidate_id"])
    if candidate is None:
        return {"error": "UNKNOWN_CANDIDATE"}
    return _reassess(tc, candidate, "CHECK_TRANSITION", new_evidence=False)


HANDLERS = {"read_history": read_history, "read_events": read_events, "inspect_window": inspect_window,
            "assess_event": assess_event}


def parse_arguments(name: str, raw: str) -> tuple[dict[str, Any] | None, str | None]:
    if name not in VALIDATORS:
        return None, "UNKNOWN_TOOL"
    try:
        args = json.loads(raw or "{}")
    except ValueError:
        return None, "MALFORMED_ARGUMENTS"
    if not isinstance(args, dict) or next(VALIDATORS[name].iter_errors(args), None) is not None:
        return None, "INVALID_ARGUMENTS"
    for value in args.values():
        if isinstance(value, str) and ("/" in value or "\\" in value or ":" in value):
            return None, "INVALID_ARGUMENTS"  # no paths or URLs are accepted anywhere
    if "candidate_id" in args and not str(args["candidate_id"]).startswith("K"):
        return None, "INVALID_ARGUMENTS"
    return args, None


def run_tool(tc: ToolContext, name: str, raw_arguments: str) -> dict[str, Any]:
    args, error = parse_arguments(name, raw_arguments)
    if error:
        return {"error": error}
    assert args is not None
    if args.get("object_id", tc.object_id) != tc.object_id:
        return {"error": "WRONG_TARGET"}
    tc.ctx.emit("tool.started", {"tool": name})
    result = HANDLERS[name](tc, args)
    tc.ctx.emit("tool.completed", {"tool": name, "ok": "error" not in result})
    return result
