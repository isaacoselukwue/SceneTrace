from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from pydantic import ValidationError

from reference_core.claims import UNKNOWN_MESSAGES, ClaimValidationError, render_claim, timestamp, validate_claim

from ..domain import AnswerDraft, Claim, EventRevision, Plan
from ..storage import Storage
from ..store import AnalysisStore

ACTIONS = ("pick_up", "put_down", "place_in_zone")


@dataclass
class Compiled:
    claims: list[Claim] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)
    unknown_codes: list[str] = field(default_factory=list)
    sentences: list[dict[str, Any]] = field(default_factory=list)


def relevant_events(plan: Plan, events: dict[str, EventRevision]) -> list[EventRevision]:
    revisions = sorted(events.values(), key=lambda r: (r.record.t_start_ms, int(r.record.event_id[1:])))
    if plan.intent == "actor":
        return [r for r in revisions if r.record.event_type in ACTIONS]
    if plan.intent == "last_seen":
        return [r for r in revisions if r.record.event_type == "last_seen"]
    if plan.intent == "interaction_window" and plan.t_start_ms is not None and plan.t_end_ms is not None:
        return [r for r in revisions if r.record.event_type in ACTIONS
                and r.record.t_end_ms >= plan.t_start_ms and r.record.t_start_ms <= plan.t_end_ms]
    return revisions


def allowed_unknown_codes(plan: Plan, events: dict[str, EventRevision], *, provider_failed: bool,
                          budget_exhausted: bool) -> set[str]:
    allowed: set[str] = set()
    relevant = [r for r in relevant_events(plan, events) if r.record.status != "rejected"]
    actions = [r for r in relevant if r.record.event_type in ACTIONS]
    if plan.intent in ("actor", "history", "interaction_window") and (
            not actions or any(r.record.actor_id is None or r.record.status != "supported" for r in actions)):
        allowed.add("ACTOR_NOT_ESTABLISHED")
    if plan.intent in ("history", "last_seen"):
        allowed.add("CURRENT_LOCATION_UNKNOWN")
    hidden = {"POSSIBLE_OCCLUSION", "NO_VISIBLE_TRANSITION", "INCOMPLETE_COVERAGE", "TARGET_LOST"}
    if any(hidden & set(r.record.reason_codes) for r in relevant):
        allowed.add("INSUFFICIENT_VISIBILITY")
    if not [r for r in relevant if r.record.status == "supported"] or plan.intent == "unsupported":
        allowed.add("NO_SUPPORTED_EVENT")
    if provider_failed:
        allowed.add("PROVIDER_UNAVAILABLE")
    if budget_exhausted:
        allowed.add("BUDGET_EXHAUSTED")
    return allowed


def possible_actor_claims(plan: Plan, events: dict[str, EventRevision], cited: list[Claim]) -> list[Claim]:
    if plan.intent not in ("actor", "history"):
        return []
    cited_events = {claim.event_id for claim in cited}
    used_ids = {claim.claim_id for claim in cited}
    additions: list[Claim] = []
    for revision in relevant_events(plan, events):
        record = revision.record
        if record.status != "plausible" or record.actor_id is None or record.event_id in cited_events:
            continue
        number = len(used_ids) + 1
        while f"C{number}" in used_ids:
            number += 1
        used_ids.add(f"C{number}")
        additions.append(Claim(claim_id=f"C{number}", event_id=record.event_id, event_revision=record.revision,
                               object_id=record.object_id, predicate=record.event_type, actor_id=record.actor_id,
                               zone_id=record.zone_id, t_start_ms=record.t_start_ms, t_end_ms=record.t_end_ms,
                               certainty="possible"))
    return additions


def fallback_draft(plan: Plan, events: dict[str, EventRevision], allowed: set[str]) -> AnswerDraft:
    claims = []
    if plan.intent != "unsupported":
        for revision in relevant_events(plan, events):
            record = revision.record
            if record.status not in ("supported", "plausible") or len(claims) >= 8:
                continue
            claims.append(Claim(claim_id=f"C{len(claims) + 1}", event_id=record.event_id, event_revision=record.revision,
                                object_id=record.object_id, predicate=record.event_type, actor_id=record.actor_id,
                                zone_id=record.zone_id, t_start_ms=record.t_start_ms, t_end_ms=record.t_end_ms,
                                certainty="supported" if record.status == "supported" else "possible"))
    order = ["ACTOR_NOT_ESTABLISHED", "INSUFFICIENT_VISIBILITY", "CURRENT_LOCATION_UNKNOWN", "NO_SUPPORTED_EVENT",
             "PROVIDER_UNAVAILABLE", "BUDGET_EXHAUSTED"]
    supported = [c for c in claims if c.certainty == "supported"]
    actions = [c for c in claims if c.predicate in ACTIONS]
    needed = {
        "ACTOR_NOT_ESTABLISHED": plan.intent in ("actor", "history", "interaction_window") and not any(c.actor_id for c in actions if c.certainty == "supported"),
        "INSUFFICIENT_VISIBILITY": True,
        "CURRENT_LOCATION_UNKNOWN": True,
        "NO_SUPPORTED_EVENT": not supported,
        "PROVIDER_UNAVAILABLE": True,
        "BUDGET_EXHAUSTED": True,
    }
    codes = [c for c in order if c in allowed and needed[c]]
    return AnswerDraft(claims=claims, unknown_codes=cast(Any, codes))


def scope_sentences(plan: Plan, store: AnalysisStore, analysis_id: str, events: dict[str, EventRevision]) -> list[str]:
    sentences = []
    if plan.intent == "unsupported":
        sentences.append("SceneTrace only answers questions about the observed history of the selected object. It does not"
                         " identify people or judge intent.")
    incomplete = [c for c in store.coverage(analysis_id) if c.terminal_status != "completed"]
    if incomplete:
        sentences.append("Tracking coverage was incomplete, so missing detections are treated as unknown, not as absence.")
    if plan.intent == "interaction_window" and plan.t_start_ms is not None and plan.t_end_ms is not None:
        supported = [r for r in relevant_events(plan, events) if r.record.status == "supported"]
        if not supported and not incomplete:
            sentences.append(f"No supported interaction was found between {timestamp(plan.t_start_ms)} and"
                             f" {timestamp(plan.t_end_ms)} in the analysed footage. This does not prove that none happened.")
    return sentences


def compile_draft(draft: AnswerDraft | dict[str, Any], *, store: AnalysisStore, storage: Storage, analysis_id: str,
                  owner_id: str, object_id: str, source_sha256: str, snapshot: dict[str, int], allowed: set[str],
                  scope: list[str]) -> Compiled:
    compiled = Compiled()
    try:
        parsed = draft if isinstance(draft, AnswerDraft) else AnswerDraft.model_validate(draft)
    except ValidationError:
        compiled.rejected.append({"claim_id": "*", "stage": "validator", "reason_code": "DRAFT_SCHEMA_INVALID"})
        parsed = AnswerDraft(claims=[], unknown_codes=[])
    events = {k: v.record for k, v in store.events_at(analysis_id, snapshot).items()}
    current = store.current_events(analysis_id)
    items = store.evidence(analysis_id)
    evidence = {k: v.evidence for k, v in items.items()}
    seen: set[str] = set()
    for claim in parsed.claims:
        code = None
        if claim.claim_id in seen:
            code = "DUPLICATE_CLAIM_ID"
        elif claim.event_id in current and current[claim.event_id].record.revision != claim.event_revision:
            code = "STALE_EVENT_REVISION"
        else:
            try:
                event = validate_claim(claim, events, evidence, object_id=object_id, source_sha256=source_sha256)
                for evidence_id in event.evidence_ids:
                    item = items[evidence_id]
                    row = storage.asset_row(item.asset_id, owner_id, allow_public=True)
                    if row["owner_id"] != owner_id and not row["public"]:
                        raise ClaimValidationError("EVIDENCE_NOT_OWNED")
                    try:
                        storage.asset_path(item.asset_id, row["owner_id"], verify_digest=True)
                    except ValueError as exc:
                        raise ClaimValidationError("EVIDENCE_DIGEST_MISMATCH") from exc
                    if row["sha256"] != item.evidence.asset_sha256:
                        raise ClaimValidationError("EVIDENCE_DIGEST_MISMATCH")
            except ClaimValidationError as exc:
                code = str(exc)
            except Exception:  # noqa: BLE001
                code = "CLAIM_CHECK_FAILED"
        seen.add(claim.claim_id)
        if code:
            compiled.rejected.append({"claim_id": claim.claim_id, "stage": "validator", "reason_code": code})
        else:
            compiled.claims.append(claim)
    for code in parsed.unknown_codes:
        if code in allowed and code not in compiled.unknown_codes:
            compiled.unknown_codes.append(code)
        elif code not in allowed:
            compiled.rejected.append({"claim_id": "-", "stage": "validator", "reason_code": f"UNSUPPORTED_ABSTENTION_REASON:{code}"})
    compiled.claims.sort(key=lambda c: (c.t_start_ms, c.claim_id))
    compiled.sentences = sentences_for(compiled.claims, compiled.unknown_codes, scope, events)
    return compiled


def sentences_for(claims: list[Claim], unknown_codes: list[str], scope: list[str], events: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for claim in claims:
        event = events[claim.event_id]
        out.append({"text": render_claim(claim), "kind": "claim", "certainty": claim.certainty,
                    "citation": {"claim_id": claim.claim_id, "event_id": claim.event_id, "event_revision": claim.event_revision,
                                 "t_start_ms": claim.t_start_ms, "t_end_ms": claim.t_end_ms,
                                 "evidence_ids": list(event.evidence_ids)}})
    out += [{"text": UNKNOWN_MESSAGES[c], "kind": "unknown", "certainty": None, "citation": None} for c in unknown_codes]
    out += [{"text": s, "kind": "scope", "certainty": None, "citation": None} for s in scope]
    return out
