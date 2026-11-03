from __future__ import annotations

import json
import re
from typing import Any, Literal

from ..domain import AssessmentResult, AuditFinding, AuditResult, Candidate, EvidenceItem, Plan
from .base import FunctionCall, ModelTurn, RunContext


class FixtureAssessor:
    model_id = "fixture-assessor"

    def assess(self, ctx: RunContext, *, candidate: Candidate, frames: list[EvidenceItem], frame_bytes: dict[str, bytes],
               cue_table: dict[str, Any], purpose: str) -> AssessmentResult:
        codes = set(candidate.reason_codes)
        raw = [f.evidence.evidence_id for f in frames if not f.evidence.derived]
        distances = sorted((v, k.split(":", 1)[1]) for k, v in cue_table.items() if k.startswith("min_edge_distance_bp:"))
        nearest = next((p for _d, p in distances if p in candidate.actor_candidates), None)
        moved = (cue_table.get("displacement_before_to_after_bp") or 0) >= 7_500
        base = dict(candidate_id=candidate.candidate_id, object_id=candidate.object_id, actor_candidates=candidate.actor_candidates,
                    evidence_ids=raw, destination_zone=candidate.zone_id)
        if "PROXIMITY_ONLY" in codes:
            return AssessmentResult(**base, visible_before="yes", transition_visible="no", visible_after="yes",
                                    support_changed="no", carried_motion_visible="no", actor_assignment=None,
                                    verdict="contradicts", reason_codes=["TARGET_UNCHANGED"])
        if codes & {"POSSIBLE_OCCLUSION", "TARGET_LOST"}:
            return AssessmentResult(**base, visible_before="yes", transition_visible="no", visible_after="no",
                                    support_changed="unclear", carried_motion_visible="unclear", actor_assignment=None,
                                    verdict="insufficient", reason_codes=["POSSIBLE_OCCLUSION"])
        if moved and cue_table.get("observed_before_samples") and cue_table.get("observed_after_samples"):
            lift = candidate.candidate_type == "pick_up"
            return AssessmentResult(**base, visible_before="yes", transition_visible="yes", visible_after="yes",
                                    support_changed="yes", carried_motion_visible="yes" if lift else "no",
                                    actor_assignment=nearest, verdict="supports",
                                    reason_codes=["CLEAR_LIFT" if lift else "CLEAR_RELEASE"])
        return AssessmentResult(**base, visible_before="unclear", transition_visible="unclear", visible_after="unclear",
                                support_changed="unclear", carried_motion_visible="unclear", actor_assignment=None,
                                verdict="insufficient", reason_codes=["NO_VISIBLE_TRANSITION"])


def keyword_plan(question: str, object_id: str) -> Plan:
    q = question.lower()
    times = [int(float(m) * 1000) for m in re.findall(r"(\d+(?:\.\d+)?)\s*s\b", q)]
    times += [int(a) * 60_000 + int(b) * 1000 for a, b in re.findall(r"\b(\d{1,2}):(\d{2})\b", q)]
    if re.search(r"\b(name|identity|who is|steal|stole|theft|guilty|why)\b", q):
        intent = "unsupported"
    elif len(times) >= 2 and re.search(r"\b(between|during|from)\b", q):
        intent = "interaction_window"
    elif re.search(r"\bwho\b", q):
        intent = "actor"
    elif re.search(r"\b(where|last seen|last observed|now)\b", q):
        intent = "last_seen"
    else:
        intent = "history"
    window = sorted(times[:2]) if intent == "interaction_window" else [None, None]
    return Plan(intent=intent, target_hint=object_id, object_id=object_id, t_start_ms=window[0], t_end_ms=window[1],
                needs_person_tracks=intent in ("actor", "interaction_window"))


class FixtureInvestigatorModel:
    model_id = "fixture-investigator"

    def plan(self, ctx: RunContext, *, question: str, object_id: str, clip_duration_ms: int) -> Plan:
        return keyword_plan(question, object_id)

    def turn(self, ctx: RunContext, *, instructions: str, input_items: list[dict[str, Any]],
             previous_response_id: str | None, tools: list[dict[str, Any]]) -> ModelTurn:
        outputs = [i for i in input_items if i.get("type") == "function_call_output"]
        if not outputs:
            return ModelTurn(response_id="fixture-1", status="completed", text=None,
                             function_calls=[FunctionCall("fc-1", "read_history", json.dumps({"object_id": "O1"}))])
        history = json.loads(outputs[-1]["output"])
        claims = []
        for event in history.get("events", []):
            if event["status"] in ("supported", "plausible") and len(claims) < 8:
                claims.append({"claim_id": f"C{len(claims) + 1}", "event_id": event["event_id"],
                               "event_revision": event["revision"], "object_id": event["object_id"],
                               "predicate": event["event_type"], "actor_id": event["actor_id"], "zone_id": event["zone_id"],
                               "t_start_ms": event["t_start_ms"], "t_end_ms": event["t_end_ms"],
                               "certainty": "supported" if event["status"] == "supported" else "possible"})
        draft = {"claims": claims, "unknown_codes": [c for c in history.get("allowed_unknown_codes", [])
                                                      if c in ("CURRENT_LOCATION_UNKNOWN", "ACTOR_NOT_ESTABLISHED")]}
        return ModelTurn(response_id="fixture-2", status="completed", text=None,
                         function_calls=[FunctionCall("fc-2", "submit_claims", json.dumps(draft))])


class FixtureReviewer:
    model_id = "fixture-reviewer"

    def review(self, ctx: RunContext, *, question: str, claims: list[dict[str, Any]], evidence: list[EvidenceItem],
               frame_bytes: dict[str, bytes], cues: list[dict[str, Any]],
               mode: Literal["image_secondary", "text_secondary"]) -> AuditResult:
        findings = [AuditFinding(claim_id=c["claim_id"], verdict="pass", reason_code="CONSISTENT_RECORDS",
                                 evidence_ids=[]) for c in claims]
        return AuditResult(verdict="pass", findings=findings)
