from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EntityId = Annotated[str, Field(pattern=r"^(O|P|Z)[1-9][0-9]*$")]
EventId = Annotated[str, Field(pattern=r"^E[1-9][0-9]*$")]
EvidenceId = Annotated[str, Field(pattern=r"^V[1-9][0-9]*$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
NonNegativeInt = Annotated[int, Field(ge=0)]
EventType = Literal["first_seen", "pick_up", "put_down", "place_in_zone", "last_seen"]
EventStatus = Literal["supported", "plausible", "contested", "rejected"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Evidence(StrictModel):
    evidence_id: EvidenceId
    source_sha256: Digest
    asset_path: str
    asset_sha256: Digest
    t_ms: NonNegativeInt
    role: Literal["before", "during", "after", "observation"]
    derived: bool


class EventRecord(StrictModel):
    event_id: EventId
    revision: Annotated[int, Field(ge=1)]
    object_id: EntityId
    event_type: EventType
    actor_id: EntityId | None
    zone_id: EntityId | None
    t_start_ms: NonNegativeInt
    t_end_ms: NonNegativeInt
    status: EventStatus
    evidence_ids: Annotated[list[EvidenceId], Field(min_length=1)]
    reason_codes: list[str]

    @model_validator(mode="after")
    def valid_event(self) -> EventRecord:
        if not self.object_id.startswith("O"):
            raise ValueError("An event object must use an O identifier")
        if self.actor_id is not None and not self.actor_id.startswith("P"):
            raise ValueError("An actor must use a P identifier")
        if self.zone_id is not None and not self.zone_id.startswith("Z"):
            raise ValueError("A zone must use a Z identifier")
        if self.t_end_ms < self.t_start_ms:
            raise ValueError("Event interval is reversed")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("Duplicate evidence identifiers")
        if self.event_type == "place_in_zone" and self.zone_id is None:
            raise ValueError("place_in_zone requires a zone")
        if self.event_type != "place_in_zone" and self.zone_id is not None:
            raise ValueError("Only place_in_zone carries a zone")
        if self.event_type in ("first_seen", "last_seen") and self.actor_id is not None:
            raise ValueError("A sighting does not establish an actor")
        return self


class Claim(StrictModel):
    claim_id: Annotated[str, Field(pattern=r"^C[1-9][0-9]*$")]
    event_id: EventId
    event_revision: Annotated[int, Field(ge=1)]
    object_id: EntityId
    predicate: EventType
    actor_id: EntityId | None
    zone_id: EntityId | None
    t_start_ms: NonNegativeInt
    t_end_ms: NonNegativeInt
    certainty: Literal["supported", "possible"]


class AnswerDraft(StrictModel):
    claims: Annotated[list[Claim], Field(max_length=8)]
    unknown_codes: list[Literal[
        "ACTOR_NOT_ESTABLISHED", "CURRENT_LOCATION_UNKNOWN", "INSUFFICIENT_VISIBILITY",
        "TARGET_AMBIGUOUS", "BUDGET_EXHAUSTED", "PROVIDER_UNAVAILABLE", "NO_SUPPORTED_EVENT"
    ]]


class AuditFinding(StrictModel):
    claim_id: str
    verdict: Literal["pass", "reject", "needs_evidence"]
    reason_code: str
    evidence_ids: list[EvidenceId]


class AuditResult(StrictModel):
    verdict: Literal["pass", "reject", "needs_evidence"]
    findings: list[AuditFinding]


class Plan(StrictModel):
    intent: Literal["history", "actor", "last_seen", "interaction_window", "unsupported"]
    target_hint: str
    object_id: EntityId | None
    t_start_ms: NonNegativeInt | None
    t_end_ms: NonNegativeInt | None
    needs_person_tracks: bool
