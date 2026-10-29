"""Domain contracts shared by media, providers, the event engine and the investigator.

The record models in reference_core.models (EventRecord, Evidence, Claim,
AnswerDraft, AuditResult, Plan) are authoritative and re-exported unchanged.
Application wrappers add run metadata without mutating those records.

Conventions:
- t_ms: integer milliseconds on the normalised clip timeline (proxy frame k is at
  round(k * 1000 / proxy_fps)).
- Boxes: integer pixel rectangles [x0, y0, x1, y1] with exclusive right/bottom in the
  proxy coordinate system ("proxy_px"). SAM's inclusive x2/y2 are converted at the
  adapter boundary (meta_sam_parser already returns half-open boxes).
- Entity IDs: O1 (selected object), P1..Pn (clip-local person tracks), Z1..Zn (zones).
- Candidate IDs: K1..Kn. Evidence IDs: V1..Vn. Event IDs: E1..En. Claim IDs: C1..Cn.
- Confidence: integer basis points or None. None is not zero.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from reference_core.models import (  # noqa: F401  (re-exported contracts)
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
# Engine-only codes may appear in EventRecord.reason_codes and candidate cues.
ENGINE_CODES = (
    "PROXIMITY_ONLY", "TARGET_STATIONARY", "TARGET_MOVED", "TARGET_LOST", "ACTOR_NOT_ESTABLISHED",
    "MODEL_SUPPORT_OVERRULED", "ASSESSMENT_UNAVAILABLE", "PARTIAL_SCOPE", "BOUNDARY_WINDOW",
    "ZONE_OVERLAP_ONLY", "REVISED_BY_INSPECTION",
)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# Media ---------------------------------------------------------------------
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
    source_width: int  # displayed size after rotation
    source_height: int
    rotation_deg: int
    source_time_base: str  # e.g. "1/12800"
    source_variable_frame_rate: bool
    entries: list[FrameMapEntry]

    def entry(self, proxy_frame_index: int) -> FrameMapEntry:
        return self.entries[proxy_frame_index]

    def frame_for_t_ms(self, t_ms: int) -> int:
        """Nearest proxy frame for a clip time, clamped to the clip."""
        k = round(t_ms * self.proxy_fps / 1000)
        return max(0, min(len(self.entries) - 1, k))


class ProxyVideo(Strict):
    """A normalised proxy registered for a clip. Paths are absolute server paths, never sent to clients."""

    clip_id: str
    owner_id: str
    path: str
    sha256: str
    width: int
    height: int
    fps: int
    frame_count: int
    frame_map_sha256: str


# Tracking ------------------------------------------------------------------
class ConceptPrompt(Strict):
    kind: Literal["concept"] = "concept"
    phrase: str = Field(min_length=1, max_length=60)


class BoxPrompt(Strict):
    """Seed one object with a box on one proxy frame (exclusive right/bottom, proxy pixels)."""

    kind: Literal["box"] = "box"
    object_id: int = 1
    frame_index: int = Field(ge=0)
    box: BoxXYXY
    propagation: Literal["both", "forward", "backward"] = "both"


class ProviderBox(Strict):
    """One provider record after parsing, in proxy pixels, exclusive right/bottom."""

    proxy_frame_index: int
    provider_object_id: str
    box: BoxXYXY
    frame_width: int
    frame_height: int
    confidence_bp: int | None
    mask_svg: str | None = None  # SVG path in proxy pixel coordinates
    mask_area_px: int | None = None
    mask_centroid_milli: tuple[int, int] | None = None  # centroid x/y * 1000


class Coverage(Strict):
    request_key: str
    concept: str
    role: Literal["selection", "target", "person"]
    requested_start_frame: int
    requested_end_frame: int
    terminal_status: Literal["completed", "incomplete", "failed", "not_run"]
    missing_ranges: list[tuple[int, int]] = []  # inclusive proxy frame ranges whose state is unknown


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
    track_key: str  # f"{clip_id}:{request_key}:{concept}:{provider_object_id}"
    proxy_frame_index: int
    t_ms: int
    width_px: int
    height_px: int
    box: BoxXYXY | None
    centre_milli: tuple[int, int] | None  # mask centroid if available, else box centre, * 1000
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


# Event engine ----------------------------------------------------------------
class Candidate(Strict):
    candidate_id: CandidateId
    candidate_type: EventType
    object_id: str = "O1"
    t_start_ms: int
    t_end_ms: int
    window_start_ms: int  # evidence window, normally 1.5 s either side, clamped
    window_end_ms: int
    actor_candidates: list[PersonId] = []
    zone_id: ZoneId | None = None
    cues: dict[str, int | str | bool | None | list[str]] = {}
    reason_codes: list[str] = []
    evidence_ids: list[EvidenceId] = []


class EvidenceItem(Strict):
    """Application wrapper around reference_core Evidence with frame mapping and asset IDs."""

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
    """Closed structured assessment of one candidate. Never rendered as prose."""

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
    basis: dict[str, str | int | list[str] | None]  # assessment ids, rule outcome codes
    record_sha256: str
    created_at: str


# Usage ---------------------------------------------------------------------
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
