from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..domain import Claim, EventRecord, MediaKind, RunMode

ReviewMode = Literal["image_secondary", "text_secondary", "not_run"]
ReviewVerdict = Literal["pass", "reject", "needs_evidence", "not_run", "unavailable", "mixed"]
JobState = Literal["queued", "running", "needs_selection", "completed", "partial", "failed",
                   "cancel_requested", "cancelled", "interrupted"]
CostStatus = Literal["estimated", "provider_usage_based", "unknown", "replayed", "none"]


class Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ErrorBody(Out):
    code: str
    message: str
    retryable: bool
    request_id: str


class ErrorEnvelope(Out):
    error: ErrorBody


class SessionCreate(In):
    access_code: str | None = Field(default=None, max_length=200)


class SessionOut(Out):
    scope: Literal["owner", "public"]
    csrf_token: str
    expires_at: str
    live_uploads_enabled: bool


class ModelCapabilityOut(Out):
    role: Literal["tracking", "investigator", "reviewer"]
    model_id: str
    status: Literal["passed", "failed", "unavailable", "not_tested"]
    checked_at: str | None


class LimitsOut(Out):
    max_upload_bytes: int
    max_clip_duration_ms: int
    max_supported_people: int
    analysis_cap_microusd: int
    question_cap_microusd: int
    daily_cap_microusd: int
    max_pending_jobs: int


class CapabilitiesOut(Out):
    project_owner: str
    default_run_mode: RunMode
    available_run_modes: list[RunMode]
    live_available: bool
    live_block_reason: str | None
    review_mode: ReviewMode
    models: list[ModelCapabilityOut]
    limits: LimitsOut
    demo_read_only: bool
    build: dict[str, str | None]


class HealthOut(Out):
    status: Literal["ok"]


class ReadyOut(Out):
    status: Literal["ready", "degraded"]
    database: bool
    storage: bool
    worker: bool
    ffmpeg: bool


class ClipOut(Out):
    clip_id: str
    status: Literal["uploaded", "normalising", "ready", "rejected", "failed", "deleted", "expired"]
    media_kind: MediaKind
    display_name: str
    source_sha256: str
    source_bytes: int
    duration_ms: int | None
    source_width: int | None
    source_height: int | None
    rotation_deg: int | None
    proxy_width: int | None
    proxy_height: int | None
    proxy_fps: int | None
    proxy_frame_count: int | None
    video_url: str | None
    reject_code: str | None
    normalise_job_id: str | None
    analyses: list[str]
    created_at: str
    expires_at: str | None
    demo: bool


class FrameOut(Out):
    t_ms: int
    proxy_frame_index: int
    width: int
    height: int
    image_url: str


class TargetConceptIn(In):
    kind: Literal["concept"]
    phrase: str = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z0-9][A-Za-z0-9 \-']*$")
    frame_index: int = Field(default=0, ge=0)


class TargetBoxIn(In):
    kind: Literal["box"]
    frame_index: int = Field(ge=0)
    proxy_width_px: int = Field(gt=0)
    proxy_height_px: int = Field(gt=0)
    xyxy_exclusive: Annotated[list[int], Field(min_length=4, max_length=4)]
    label: str = Field(default="selected object", min_length=1, max_length=40, pattern=r"^[A-Za-z0-9][A-Za-z0-9 \-']*$")


class ZoneIn(In):
    label: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9][A-Za-z0-9 \-']*$")
    polygon: Annotated[list[Annotated[list[int], Field(min_length=2, max_length=2)]], Field(min_length=3, max_length=16)]


class AnalysisCreate(In):
    target: TargetConceptIn | TargetBoxIn = Field(discriminator="kind")
    zones: list[ZoneIn] = Field(default_factory=list, max_length=4)
    mode: RunMode
    idempotency_key: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_\-]+$")


class SelectionBind(In):
    selection_id: str = Field(min_length=1, max_length=80)
    idempotency_key: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_\-]+$")


class JobAccepted(Out):
    job_id: str
    analysis_id: str | None = None
    investigation_id: str | None = None
    export_id: str | None = None
    state: JobState


class SelectionOut(Out):
    selection_id: str
    proxy_frame_index: int
    t_ms: int
    box: list[int]
    thumbnail_url: str | None
    confidence_bp: int | None


class EntityOut(Out):
    entity_id: str
    kind: Literal["object", "person", "zone"]
    label: str
    first_t_ms: int | None
    last_t_ms: int | None
    identity_warning: bool
    polygon: list[list[int]] | None = None


class EventRevisionOut(Out):
    record: EventRecord
    record_sha256: str
    created_at: str
    basis: dict


class EventOut(Out):
    current: EventRevisionOut
    history: list[EventRevisionOut]
    candidate_id: str | None
    status_meaning: str


class CandidateOut(Out):
    candidate_id: str
    candidate_type: str
    t_start_ms: int
    t_end_ms: int
    window_start_ms: int
    window_end_ms: int
    actor_candidates: list[str]
    zone_id: str | None
    reason_codes: list[str]
    evidence_ids: list[str]
    cues: dict
    event_id: str | None
    outcome: Literal["supported", "plausible", "contested", "rejected", "pending"]


class EvidenceOut(Out):
    evidence_id: str
    candidate_id: str | None
    role: Literal["before", "during", "after", "observation"]
    kind: Literal["frame", "crop", "annotated", "storyboard"]
    t_ms: int
    proxy_frame_index: int
    source_frame_index: int
    source_pts_ms: int
    derived: bool
    asset_sha256: str
    image_url: str
    width: int
    height: int


class AssessmentOut(Out):
    assessment_id: str
    candidate_id: str
    purpose: str
    model_id: str
    run_mode: RunMode
    status: str
    result: dict | None
    created_at: str


class CoverageOut(Out):
    request_key: str
    role: str
    concept: str
    requested_start_ms: int
    requested_end_ms: int
    terminal_status: Literal["completed", "incomplete", "failed", "not_run"]
    missing_ranges_ms: list[list[int]]


class ProviderCallOut(Out):
    provider_call_id: str
    operation: str
    model_id: str
    attempt: int
    state: str
    cost_status: str
    reserved_microusd: int
    actual_microusd: int | None
    elapsed_ms: int | None
    request_id: str | None
    error_code: str | None
    created_at: str


class UsageOut(Out):
    total_microusd: int
    cost_status: CostStatus
    provider_calls: int
    unknown_cost_calls: int
    calls: list[ProviderCallOut]
    rate_card_revision: str | None


class ProvenanceOut(Out):
    run_mode: RunMode
    models: dict[str, str]
    parser_version: str | None
    preprocessing_version: str | None
    recorded_at: str | None
    recorded_from: str | None


class AnalysisOut(Out):
    analysis_id: str
    clip_id: str
    job_id: str | None
    status: Literal["queued", "running", "needs_selection", "completed", "partial", "failed", "cancelled", "interrupted"]
    run_mode: RunMode
    media_kind: MediaKind
    source_sha256: str
    target: dict
    zones: list[EntityOut]
    selections: list[SelectionOut]
    entities: list[EntityOut]
    events: list[EventOut]
    candidates: list[CandidateOut]
    evidence: list[EvidenceOut]
    assessments: list[AssessmentOut]
    coverage: list[CoverageOut]
    provenance: ProvenanceOut
    usage: UsageOut
    timings_ms: dict[str, int]
    partial_reasons: list[str]
    created_at: str
    completed_at: str | None
    demo: bool


class ObservationOut(Out):
    entity_id: str
    proxy_frame_index: int
    t_ms: int
    box: list[int] | None
    mask_svg: str | None
    detection_state: Literal["observed", "not_detected", "unknown"]
    confidence_bp: int | None
    identity_warning: bool


class VisibilitySpanOut(Out):
    state: Literal["observed", "not_detected", "unknown"]
    start_ms: int
    end_ms: int


class VisibilityOut(Out):
    analysis_id: str
    entity_id: str
    duration_ms: int
    spans: list[VisibilitySpanOut]


class ObservationsPage(Out):
    analysis_id: str
    t0_ms: int
    t1_ms: int
    frame_width: int
    frame_height: int
    observations: list[ObservationOut]
    next_t0_ms: int | None


class QuestionCreate(In):
    question: str = Field(min_length=1, max_length=300)
    object_id: str = Field(default="O1", pattern=r"^O[1-9][0-9]*$")
    idempotency_key: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_\-]+$")


class CitationOut(Out):
    claim_id: str
    event_id: str
    event_revision: int
    t_start_ms: int
    t_end_ms: int
    evidence_ids: list[str]


class AnswerSentenceOut(Out):
    text: str
    kind: Literal["claim", "unknown", "scope"]
    certainty: Literal["supported", "possible"] | None
    citation: CitationOut | None


class RejectedClaimOut(Out):
    claim_id: str
    stage: Literal["validator", "reviewer"]
    reason_code: str


class ReviewFindingOut(Out):
    claim_id: str
    verdict: Literal["pass", "reject", "needs_evidence"]
    reason_code: str
    evidence_ids: list[str]


class InvestigationOut(Out):
    investigation_id: str
    analysis_id: str
    job_id: str | None
    question: str
    object_id: str
    status: Literal["queued", "running", "completed", "partial", "failed", "cancelled", "interrupted"]
    run_mode: RunMode
    intent: str | None
    sentences: list[AnswerSentenceOut]
    claims: list[Claim]
    rejected_claims: list[RejectedClaimOut]
    unknown_codes: list[str]
    answer_source: Literal["investigator", "deterministic_fallback", "abstained", "pending"]
    review_mode: ReviewMode
    review_verdict: ReviewVerdict
    review_findings: list[ReviewFindingOut]
    snapshot: dict
    limits_used: dict[str, int]
    original_processing_cost_microusd: int
    current_request_cost_microusd: int
    cost_status: CostStatus
    timings_ms: dict[str, int]
    created_at: str
    completed_at: str | None
    recorded_at: str | None


class JobOut(Out):
    job_id: str
    kind: Literal["normalise", "analysis", "question", "export"]
    state: JobState
    stage: str | None
    run_mode: RunMode | None
    clip_id: str | None
    analysis_id: str | None
    investigation_id: str | None
    export_id: str | None
    error: ErrorBody | None
    partial_outcome: dict | None
    created_at: str
    started_at: str | None
    finished_at: str | None


class JobEventOut(Out):
    job_id: str
    seq: int
    type: Literal["job.queued", "stage.started", "stage.progress", "stage.completed", "tool.started",
                  "tool.completed", "evidence.created", "claim.rejected", "review.completed", "budget.updated",
                  "job.completed", "job.partial", "job.failed", "job.cancelled", "job.needs_selection"]
    stage: str | None
    summary: str
    elapsed_ms: int
    mode: RunMode | None
    created_at: str
    data: dict


class RecordedQuestionOut(Out):
    investigation_id: str
    question: str


class DemoOut(Out):
    demo_id: str
    title: str
    description: str
    analysis_id: str
    clip_id: str
    media_kind: MediaKind
    run_mode: Literal["recorded_real"]
    recorded_at: str | None
    models: dict[str, str]
    original_processing_cost_microusd: int
    cost_status: CostStatus
    questions: list[RecordedQuestionOut]
    poster_url: str | None


class ExportCreate(In):
    idempotency_key: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_\-]+$")


class ExportOut(Out):
    export_id: str
    analysis_id: str
    status: Literal["queued", "building", "sealed", "failed", "revoked"]
    head_digest: str | None
    trusted_head: str | None
    trusted_head_source: str | None
    file_count: int | None
    bytes: int | None
    download_url: str | None
    created_at: str
    sealed_at: str | None


class VerifyIn(In):
    expected_head: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class VerifyOut(Out):
    export_id: str
    integrity: Literal["valid", "failed"]
    anchored: bool
    anchor_source: Literal["registry", "supplied", "none"]
    head: str | None
    errors: list[str]
    note: str
