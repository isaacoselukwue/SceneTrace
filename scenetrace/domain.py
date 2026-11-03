from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from reference_core.models import (  # noqa: F401
    AnswerDraft,
    AuditFinding,
    AuditResult,
    Claim,
    EventRecord,
    EventStatus,
    EventType,
    Evidence,
    Plan,
)

RunMode = Literal["fixture", "recorded_real", "live"]
MediaKind = Literal["user_upload", "synthetic_render", "fixture"]
DetectionState = Literal["observed", "not_detected", "unknown"]
YesNoUnclear = Literal["yes", "no", "unclear"]
CandidateId = Annotated[str, Field(pattern=r"^K[1-9][0-9]*$")]
PersonId = Annotated[str, Field(pattern=r"^P[1-9][0-9]*$")]
ZoneId = Annotated[str, Field(pattern=r"^Z[1-9][0-9]*$")]
EvidenceId = Annotated[str, Field(pattern=r"^V[1-9][0-9]*$")]
BoxXYXY = Annotated[list[int], Field(min_length=4, max_length=4)]

REASON_CODES = (
    "CLEAR_LIFT", "CLEAR_RELEASE", "STABLE_AFTER", "POSSIBLE_OCCLUSION", "INCOMPLETE_COVERAGE",
    "COMPETING_ACTORS", "TRACK_FRAGMENTED", "SMALL_TARGET", "NO_VISIBLE_TRANSITION", "TARGET_UNCHANGED",
)
ReasonCode = Literal[
    "CLEAR_LIFT", "CLEAR_RELEASE", "STABLE_AFTER", "POSSIBLE_OCCLUSION", "INCOMPLETE_COVERAGE",
    "COMPETING_ACTORS", "TRACK_FRAGMENTED", "SMALL_TARGET", "NO_VISIBLE_TRANSITION", "TARGET_UNCHANGED",
]
ENGINE_CODES = (
    "PROXIMITY_ONLY", "TARGET_STATIONARY", "TARGET_MOVED", "TARGET_LOST", "ACTOR_NOT_ESTABLISHED",
    "MODEL_SUPPORT_OVERRULED", "ASSESSMENT_UNAVAILABLE", "PARTIAL_SCOPE", "BOUNDARY_WINDOW",
    "ZONE_OVERLAP_ONLY", "REVISED_BY_INSPECTION",
)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FrameMapEntry(Strict):
    proxy_frame_index: int = Field(ge=0)
    t_ms: int = Field(ge=0)
    source_frame_index: int = Field(ge=0)
    source_pts: int
    source_pts_ms: int = Field(ge=0)


class FrameMap(Strict):
    schema_version: Literal[1] = 1
    proxy_fps: int
    proxy_width: int
    proxy_height: int
    source_width: int
    source_height: int
    rotation_deg: int
    source_time_base: str
    source_variable_frame_rate: bool
    entries: list[FrameMapEntry]

    def entry(self, proxy_frame_index: int) -> FrameMapEntry:
        return self.entries[proxy_frame_index]

    def frame_for_t_ms(self, t_ms: int) -> int:
        k = round(t_ms * self.proxy_fps / 1000)
        return max(0, min(len(self.entries) - 1, k))


class ProxyVideo(Strict):
    clip_id: str
    owner_id: str
    path: str
    sha256: str
    width: int
    height: int
    fps: int
    frame_count: int
    frame_map_sha256: str


class ConceptPrompt(Strict):
    kind: Literal["concept"] = "concept"
    phrase: str = Field(min_length=1, max_length=60)


class BoxPrompt(Strict):
    kind: Literal["box"] = "box"
    object_id: int = 1
    frame_index: int = Field(ge=0)
    box: BoxXYXY
    propagation: Literal["both", "forward", "backward"] = "both"


class ProviderBox(Strict):
    proxy_frame_index: int
    provider_object_id: str
    box: BoxXYXY
    frame_width: int
    frame_height: int
    confidence_bp: int | None
    mask_svg: str | None = None
    mask_area_px: int | None = None
    mask_centroid_milli: tuple[int, int] | None = None


class Coverage(Strict):
    request_key: str
    concept: str
    role: Literal["selection", "target", "person"]
    requested_start_frame: int
    requested_end_frame: int
    terminal_status: Literal["completed", "incomplete", "failed", "not_run"]
    missing_ranges: list[tuple[int, int]] = []


class TrackingResult(Strict):
    request_key: str
    provider_response_id: str | None
    model_id: str
    concept: str
    role: Literal["selection", "target", "person"]
    prompt_kind: Literal["concept", "box"]
    frame_map_digest: str
    records: list[ProviderBox]
    coverage: Coverage
    outcome: Literal["completed", "incomplete", "failed", "cancelled", "replayed"]
    provider_call_ids: list[str]
    raw_digest: str | None
    parser_version: str
    diagnostics: list[str] = []


class SelectionCandidate(Strict):
    selection_id: str
    proxy_frame_index: int
    box: BoxXYXY
    provider_object_id: str | None
    confidence_bp: int | None


class Observation(Strict):
    analysis_id: str
    request_key: str
    entity_id: str
    track_key: str
    proxy_frame_index: int
    t_ms: int
    width_px: int
    height_px: int
    box: BoxXYXY | None
    centre_milli: tuple[int, int] | None
    mask_svg: str | None
    mask_area_px: int | None
    provider_confidence_bp: int | None
    detection_state: DetectionState
    identity_warning: bool = False


class Zone(Strict):
    zone_id: ZoneId
    label: str = Field(min_length=1, max_length=40)
    polygon: list[tuple[int, int]] = Field(min_length=3, max_length=16)


class Entity(Strict):
    entity_id: str
    kind: Literal["object", "person", "zone"]
    label: str
    track_keys: list[str] = []
    first_t_ms: int | None = None
    last_t_ms: int | None = None
    identity_warning: bool = False


class Candidate(Strict):
    candidate_id: CandidateId
    candidate_type: EventType
    object_id: str = "O1"
    t_start_ms: int
    t_end_ms: int
    window_start_ms: int
    window_end_ms: int
    actor_candidates: list[PersonId] = []
    zone_id: ZoneId | None = None
    cues: dict[str, int | str | bool | None | list[str]] = {}
    reason_codes: list[str] = []
    evidence_ids: list[EvidenceId] = []


class EvidenceItem(Strict):
    evidence: Evidence
    asset_id: str
    candidate_id: str | None
    kind: Literal["frame", "crop", "annotated", "storyboard"]
    proxy_frame_index: int
    source_frame_index: int
    source_pts_ms: int
    crop_box_source_px: BoxXYXY | None = None
    width: int
    height: int


class AssessmentResult(Strict):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"description": "Closed structured assessment of one candidate. Never rendered as prose."},
    )

    candidate_id: CandidateId
    object_id: str
    visible_before: YesNoUnclear
    transition_visible: YesNoUnclear
    visible_after: YesNoUnclear
    support_changed: YesNoUnclear
    carried_motion_visible: YesNoUnclear
    actor_candidates: list[PersonId]
    actor_assignment: PersonId | None
    destination_zone: ZoneId | None
    evidence_ids: list[EvidenceId]
    verdict: Literal["supports", "contradicts", "insufficient"]
    reason_codes: list[ReasonCode]


class AssessmentRecord(Strict):
    assessment_id: str
    analysis_id: str
    candidate_id: str
    purpose: Literal["initial", "CHECK_VISIBILITY", "CHECK_ACTOR_ASSOCIATION", "CHECK_TRANSITION", "CHECK_DESTINATION"]
    model_id: str
    run_mode: RunMode
    status: Literal["completed", "invalid", "failed", "skipped"]
    result: AssessmentResult | None
    evidence_digests: list[str]
    provider_call_id: str | None
    created_at: str


class EventRevision(Strict):
    record: EventRecord
    candidate_id: str | None
    basis: dict[str, str | int | list[str] | None]
    record_sha256: str
    created_at: str


class UsageRecord(Strict):
    provider_call_id: str
    operation: str
    model_id: str
    attempt: int
    cost_status: Literal["estimated", "provider_usage_based", "unknown", "replayed", "released", "free"]
    reserved_microusd: int
    actual_microusd: int | None
    usage: dict | None
    request_id: str | None
    response_id: str | None
    http_status: int | None
    elapsed_ms: int | None
