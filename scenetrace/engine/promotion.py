from __future__ import annotations

from ..domain import AssessmentResult, Candidate, Coverage, EventRecord, Observation
from .candidates import assessment_cue_table
from .config import DEFAULT_ENGINE_CONFIG, EngineConfig


def _nearest_actor(candidate: Candidate, cues: dict) -> str | None:
    distances = [(cues.get(f"min_edge_distance_bp:{p}"), p) for p in candidate.actor_candidates]
    distances = sorted((d, p) for d, p in distances if d is not None)
    return distances[0][1] if distances else None


def promote(*, candidate: Candidate, assessment: AssessmentResult | None, observations: list[Observation],
            coverage: list[Coverage], event_id: str, revision: int, evidence_ids: list[str],
            config: EngineConfig = DEFAULT_ENGINE_CONFIG) -> tuple[EventRecord, dict]:
    del coverage
    cues = assessment_cue_table(candidate, observations, config)
    codes = set(candidate.reason_codes)
    kind = candidate.candidate_type
    actor: str | None = None
    zone: str | None = candidate.zone_id if kind == "place_in_zone" else None
    rule: str
    displacement = cues.get("displacement_before_to_after_bp")
    moved = displacement is not None and displacement >= config.movement_trigger_bp
    hidden = bool(codes & {"POSSIBLE_OCCLUSION", "TARGET_LOST", "INCOMPLETE_COVERAGE"}) or cues["target_unknown_samples"] > 0
    verdict = assessment.verdict if assessment else None
    if assessment is not None:
        codes.update(assessment.reason_codes)

    if kind in ("first_seen", "last_seen"):
        status, rule = ("supported", "OBSERVED_SIGHTING") if cues["target_observed_samples"] > 0 else ("rejected", "NO_OBSERVATION")
    elif kind == "pick_up" and ("PROXIMITY_ONLY" in codes or (not moved and not hidden)):
        status, rule = "rejected", "TARGET_STATIONARY_NOT_PICKED_UP"
        codes.add("TARGET_UNCHANGED")
        if verdict == "supports":
            codes.add("MODEL_SUPPORT_OVERRULED")
    elif hidden:
        status, rule = ("rejected", "VISIBLE_CONTRADICTION") if verdict == "contradicts" and not moved and "TARGET_LOST" not in codes \
            else ("plausible", "TRANSITION_NOT_VISIBLE")
        carrier = candidate.actor_candidates[0] if "MOVED_WITH_ACTOR" in codes and len(candidate.actor_candidates) == 1 else None
        named = assessment.actor_assignment if assessment else None
        if status == "plausible" and carrier is not None and named in (None, carrier):
            actor, rule = carrier, "MOVED_WITH_ONE_PERSON_AFTER_HIDDEN_CHANGE"
            codes.add("NO_VISIBLE_TRANSITION")
        else:
            codes.discard("MOVED_WITH_ACTOR")
            codes.update({"NO_VISIBLE_TRANSITION", "ACTOR_NOT_ESTABLISHED"} if status == "plausible" else set())
        if verdict == "supports":
            codes.add("MODEL_SUPPORT_OVERRULED")
    elif assessment is None:
        status, rule = "plausible", "ASSESSMENT_UNAVAILABLE"
        codes.add("ASSESSMENT_UNAVAILABLE")
    else:
        visible_change = assessment.transition_visible == "yes" and assessment.visible_before == "yes"
        if kind == "pick_up":
            requirement = visible_change and moved and (assessment.carried_motion_visible == "yes" or assessment.support_changed == "yes")
        else:
            requirement = visible_change and assessment.support_changed == "yes" and assessment.visible_after == "yes"
        if verdict == "supports" and requirement:
            status, rule = "supported", "VISIBLE_TRANSITION"
            if kind == "place_in_zone" and assessment.destination_zone != candidate.zone_id:
                status, rule = "plausible", "ZONE_NOT_CONFIRMED"
                codes.add("ZONE_OVERLAP_ONLY")
        elif verdict == "contradicts":
            status, rule = "contested", "MODEL_CONTRADICTS_GEOMETRY"
        else:
            status, rule = "plausible", "REQUIRED_OBSERVATION_MISSING"
        if status == "supported":
            nearest = _nearest_actor(candidate, cues)
            named = assessment.actor_assignment
            if named and named in candidate.actor_candidates and named == nearest and "COMPETING_ACTORS" not in codes:
                actor = named
            else:
                codes.add("ACTOR_NOT_ESTABLISHED")
    if status != "supported" and kind in ("pick_up", "put_down", "place_in_zone") and actor is None:
        codes.add("ACTOR_NOT_ESTABLISHED")
    record = EventRecord(event_id=event_id, revision=revision, object_id=candidate.object_id, event_type=kind,
                         actor_id=actor, zone_id=zone, t_start_ms=candidate.t_start_ms, t_end_ms=candidate.t_end_ms,
                         status=status, evidence_ids=list(evidence_ids), reason_codes=sorted(codes))
    basis = {"rule": rule, "candidate_id": candidate.candidate_id, "assessment_verdict": verdict,
             "displacement_bp": displacement, "engine_config": config.digest()[:16]}
    return record, basis


STATUS_MEANING = {
    "supported": "Supported by the available footage and checks. This is not independent proof.",
    "plausible": "Consistent with the footage, but a required observation is missing.",
    "contested": "The available evidence conflicts.",
    "rejected": "Contradicted by the visible evidence for this candidate.",
}
