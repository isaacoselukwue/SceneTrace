from __future__ import annotations

from collections.abc import Mapping

from .models import AnswerDraft, Claim, EventRecord, Evidence


class ClaimValidationError(ValueError):
    pass


def validate_claim(
    claim: Claim,
    events: Mapping[str, EventRecord],
    evidence: Mapping[str, Evidence],
    *,
    object_id: str,
    source_sha256: str,
) -> EventRecord:
    event = events.get(claim.event_id)
    if event is None:
        raise ClaimValidationError("EVENT_NOT_FOUND")
    if event.object_id != object_id or claim.object_id != object_id:
        raise ClaimValidationError("WRONG_TARGET")
    checks = (
        (claim.event_revision, event.revision),
        (claim.predicate, event.event_type),
        (claim.actor_id, event.actor_id),
        (claim.zone_id, event.zone_id),
        (claim.t_start_ms, event.t_start_ms),
        (claim.t_end_ms, event.t_end_ms),
    )
    if any(left != right for left, right in checks):
        raise ClaimValidationError("CLAIM_EVENT_MISMATCH")
    if event.status == "rejected":
        raise ClaimValidationError("REJECTED_EVENT")
    if claim.certainty == "supported" and event.status != "supported":
        raise ClaimValidationError("CERTAINTY_EXCEEDS_EVIDENCE")
    for evidence_id in event.evidence_ids:
        item = evidence.get(evidence_id)
        if item is None:
            raise ClaimValidationError("EVIDENCE_NOT_FOUND")
        if item.source_sha256 != source_sha256:
            raise ClaimValidationError("EVIDENCE_FROM_ANOTHER_CLIP")
    return event


def timestamp(t_ms: int) -> str:
    minutes, remainder = divmod(t_ms, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"


def render_claim(claim: Claim) -> str:
    actor = claim.actor_id or "An unassigned actor"
    actions = {
        "first_seen": f"{claim.object_id} was first observed",
        "last_seen": f"{claim.object_id} was last observed",
        "pick_up": f"{actor} picked up {claim.object_id}",
        "put_down": f"{actor} put down {claim.object_id}",
        "place_in_zone": f"{actor} placed {claim.object_id} in {claim.zone_id}",
    }
    span = timestamp(claim.t_start_ms)
    if claim.t_end_ms != claim.t_start_ms:
        span += f" to {timestamp(claim.t_end_ms)}"
    prefix = "Possible event: " if claim.certainty == "possible" else ""
    return f"{prefix}{actions[claim.predicate]} at {span} [{claim.event_id}]."


UNKNOWN_MESSAGES = {
    "ACTOR_NOT_ESTABLISHED": "The available evidence does not establish an actor.",
    "CURRENT_LOCATION_UNKNOWN": "The object's current location is unknown.",
    "INSUFFICIENT_VISIBILITY": "Visibility is insufficient to establish what happened.",
    "TARGET_AMBIGUOUS": "Select the intended object before continuing.",
    "BUDGET_EXHAUSTED": "The investigation stopped at its configured budget limit.",
    "PROVIDER_UNAVAILABLE": "The required model service is unavailable.",
    "NO_SUPPORTED_EVENT": "No supported event is available for this question.",
}


def compile_answer(
    draft: AnswerDraft,
    events: Mapping[str, EventRecord],
    evidence: Mapping[str, Evidence],
    *,
    object_id: str,
    source_sha256: str,
    allowed_unknown_codes: set[str],
) -> list[str]:
    if len({c.claim_id for c in draft.claims}) != len(draft.claims):
        raise ClaimValidationError("DUPLICATE_CLAIM_ID")
    if not set(draft.unknown_codes).issubset(allowed_unknown_codes):
        raise ClaimValidationError("UNSUPPORTED_ABSTENTION_REASON")
    for claim in draft.claims:
        validate_claim(claim, events, evidence, object_id=object_id, source_sha256=source_sha256)
    return [render_claim(c) for c in draft.claims] + [UNKNOWN_MESSAGES[c] for c in draft.unknown_codes]
