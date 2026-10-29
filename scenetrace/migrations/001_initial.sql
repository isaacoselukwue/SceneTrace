-- SceneTrace schema v1.
-- JSON columns hold DTOs with an explicit schema_version inside the payload where relevant.
-- Times: operational timestamps are UTC ISO-8601 text; clip positions are integer milliseconds.
-- Money: integer micro-US dollars. Scores: integer basis points.

CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    csrf_token TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('owner', 'public')),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE INDEX sessions_owner ON sessions(owner_id);

CREATE TABLE clips (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('uploaded', 'normalising', 'ready', 'rejected', 'failed', 'deleted', 'expired')),
    media_kind TEXT NOT NULL CHECK (media_kind IN ('user_upload', 'synthetic_render', 'fixture')),
    display_name TEXT NOT NULL,
    consent_ack INTEGER NOT NULL CHECK (consent_ack IN (0, 1)),
    source_sha256 TEXT NOT NULL,
    source_bytes INTEGER NOT NULL,
    source_container TEXT,
    source_codec TEXT,
    source_width INTEGER,
    source_height INTEGER,
    rotation_deg INTEGER,
    source_fps_num INTEGER,
    source_fps_den INTEGER,
    duration_ms INTEGER,
    proxy_sha256 TEXT,
    proxy_width INTEGER,
    proxy_height INTEGER,
    proxy_fps INTEGER,
    proxy_frame_count INTEGER,
    frame_map_sha256 TEXT,
    preprocessing_version TEXT,
    manifest_json TEXT,
    reject_code TEXT,
    demo_id TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    deleted_at TEXT,
    upload_ms INTEGER,
    normalise_ms INTEGER
);
CREATE INDEX clips_owner ON clips(owner_id, created_at);

CREATE TABLE assets (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    clip_id TEXT NOT NULL REFERENCES clips(id),
    analysis_id TEXT,
    kind TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    media_type TEXT NOT NULL,
    derived INTEGER NOT NULL CHECK (derived IN (0, 1)),
    public INTEGER NOT NULL DEFAULT 0 CHECK (public IN (0, 1)),
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    UNIQUE (owner_id, rel_path)
);
CREATE INDEX assets_clip ON assets(clip_id);
CREATE INDEX assets_digest ON assets(owner_id, sha256);

CREATE TABLE analyses (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    clip_id TEXT NOT NULL REFERENCES clips(id),
    run_mode TEXT NOT NULL CHECK (run_mode IN ('fixture', 'recorded_real', 'live')),
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'needs_selection', 'completed', 'partial', 'failed', 'cancelled', 'interrupted')),
    target_json TEXT NOT NULL,
    zones_json TEXT NOT NULL DEFAULT '[]',
    selected_selection_id TEXT,
    models_json TEXT NOT NULL,
    parser_version TEXT,
    preprocessing_version TEXT,
    rate_card_revision TEXT,
    head_digest TEXT,
    partial_reasons_json TEXT NOT NULL DEFAULT '[]',
    timings_json TEXT NOT NULL DEFAULT '{}',
    recorded_from TEXT,
    recorded_at TEXT,
    demo_id TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX analyses_clip ON analyses(clip_id);
CREATE INDEX analyses_owner ON analyses(owner_id, created_at);

CREATE TABLE selections (
    id TEXT PRIMARY KEY,
    analysis_id TEXT NOT NULL REFERENCES analyses(id),
    proxy_frame_index INTEGER NOT NULL,
    x0 INTEGER NOT NULL, y0 INTEGER NOT NULL, x1 INTEGER NOT NULL, y1 INTEGER NOT NULL,
    provider_object_id TEXT,
    provider_confidence_bp INTEGER,
    thumbnail_asset_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX selections_analysis ON selections(analysis_id);

CREATE TABLE tracking_requests (
    request_key TEXT PRIMARY KEY,
    analysis_id TEXT NOT NULL REFERENCES analyses(id),
    owner_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('selection', 'target', 'person')),
    concept TEXT NOT NULL,
    prompt_kind TEXT NOT NULL CHECK (prompt_kind IN ('concept', 'box')),
    model_id TEXT NOT NULL,
    cache_key TEXT NOT NULL,
    provider_response_id TEXT,
    provider_call_id TEXT,
    outcome TEXT NOT NULL CHECK (outcome IN ('completed', 'incomplete', 'failed', 'cancelled', 'replayed')),
    raw_asset_id TEXT,
    raw_sha256 TEXT,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX tracking_cache ON tracking_requests(owner_id, cache_key);

CREATE TABLE observations (
    analysis_id TEXT NOT NULL REFERENCES analyses(id),
    request_key TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    track_key TEXT NOT NULL,
    proxy_frame_index INTEGER NOT NULL,
    t_ms INTEGER NOT NULL,
    width_px INTEGER NOT NULL,
    height_px INTEGER NOT NULL,
    x0 INTEGER, y0 INTEGER, x1 INTEGER, y1 INTEGER,
    centre_x_milli INTEGER,
    centre_y_milli INTEGER,
    mask_svg TEXT,
    mask_area_px INTEGER,
    provider_confidence_bp INTEGER,
    detection_state TEXT NOT NULL CHECK (detection_state IN ('observed', 'not_detected', 'unknown')),
    identity_warning INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (analysis_id, entity_id, proxy_frame_index)
);
CREATE INDEX observations_time ON observations(analysis_id, t_ms);

CREATE TABLE coverage (
    analysis_id TEXT NOT NULL REFERENCES analyses(id),
    request_key TEXT NOT NULL,
    concept TEXT NOT NULL,
    role TEXT NOT NULL,
    requested_start_frame INTEGER NOT NULL,
    requested_end_frame INTEGER NOT NULL,
    terminal_status TEXT NOT NULL CHECK (terminal_status IN ('completed', 'incomplete', 'failed', 'not_run')),
    missing_ranges_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (analysis_id, request_key)
);

CREATE TABLE entities (
    analysis_id TEXT NOT NULL REFERENCES analyses(id),
    entity_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('object', 'person', 'zone')),
    label TEXT NOT NULL,
    track_keys_json TEXT NOT NULL DEFAULT '[]',
    first_t_ms INTEGER,
    last_t_ms INTEGER,
    polygon_json TEXT,
    identity_warning INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (analysis_id, entity_id)
);

CREATE TABLE candidates (
    analysis_id TEXT NOT NULL REFERENCES analyses(id),
    candidate_id TEXT NOT NULL,
    candidate_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    t_start_ms INTEGER NOT NULL,
    t_end_ms INTEGER NOT NULL,
    window_start_ms INTEGER NOT NULL,
    window_end_ms INTEGER NOT NULL,
    record_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (analysis_id, candidate_id)
);

CREATE TABLE evidence (
    analysis_id TEXT NOT NULL REFERENCES analyses(id),
    evidence_id TEXT NOT NULL,
    candidate_id TEXT,
    asset_id TEXT NOT NULL REFERENCES assets(id),
    record_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (analysis_id, evidence_id)
);

CREATE TABLE assessments (
    id TEXT PRIMARY KEY,
    analysis_id TEXT NOT NULL REFERENCES analyses(id),
    candidate_id TEXT NOT NULL,
    purpose TEXT NOT NULL,
    model_id TEXT NOT NULL,
    run_mode TEXT NOT NULL,
    provider_call_id TEXT,
    result_json TEXT,
    status TEXT NOT NULL CHECK (status IN ('completed', 'invalid', 'failed', 'skipped')),
    evidence_digests_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE INDEX assessments_candidate ON assessments(analysis_id, candidate_id);

CREATE TABLE event_revisions (
    analysis_id TEXT NOT NULL REFERENCES analyses(id),
    event_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    candidate_id TEXT,
    record_json TEXT NOT NULL,
    basis_json TEXT NOT NULL DEFAULT '{}',
    record_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (analysis_id, event_id, revision)
);

CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('normalise', 'analysis', 'question', 'export')),
    clip_id TEXT,
    analysis_id TEXT,
    investigation_id TEXT,
    export_id TEXT,
    state TEXT NOT NULL CHECK (state IN ('queued', 'running', 'needs_selection', 'completed', 'partial', 'failed', 'cancel_requested', 'cancelled', 'interrupted')),
    stage TEXT,
    run_mode TEXT,
    idempotency_key TEXT,
    payload_sha256 TEXT,
    error_code TEXT,
    error_message TEXT,
    partial_outcome_json TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    deadline_ms INTEGER
);
CREATE UNIQUE INDEX jobs_idempotency ON jobs(owner_id, kind, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX jobs_state ON jobs(state, created_at);

CREATE TABLE job_events (
    job_id TEXT NOT NULL REFERENCES jobs(id),
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (job_id, seq)
);

CREATE TABLE investigations (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    analysis_id TEXT NOT NULL REFERENCES analyses(id),
    job_id TEXT,
    question TEXT NOT NULL,
    object_id TEXT NOT NULL,
    run_mode TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'partial', 'failed', 'cancelled', 'interrupted')),
    plan_json TEXT,
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    draft_json TEXT,
    result_json TEXT,
    review_mode TEXT NOT NULL DEFAULT 'not_run' CHECK (review_mode IN ('image_secondary', 'text_secondary', 'not_run')),
    review_verdict TEXT NOT NULL DEFAULT 'not_run',
    limits_json TEXT NOT NULL DEFAULT '{}',
    timings_json TEXT NOT NULL DEFAULT '{}',
    recorded_from TEXT,
    recorded_at TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX investigations_analysis ON investigations(analysis_id, created_at);

CREATE TABLE provider_calls (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    job_id TEXT,
    analysis_id TEXT,
    investigation_id TEXT,
    scope TEXT NOT NULL CHECK (scope IN ('cold', 'warm', 'eval', 'spike', 'cleanup')),
    operation TEXT NOT NULL,
    model_id TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    run_mode TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('reserved', 'dispatched', 'settled', 'released', 'unknown', 'replayed')),
    reserved_microusd INTEGER NOT NULL,
    actual_microusd INTEGER,
    cost_status TEXT NOT NULL CHECK (cost_status IN ('estimated', 'provider_usage_based', 'unknown', 'replayed', 'released', 'free')),
    usage_json TEXT,
    request_id TEXT,
    response_id TEXT,
    http_status INTEGER,
    error_code TEXT,
    rate_card_revision TEXT NOT NULL,
    scope_keys_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    dispatched_at TEXT,
    finished_at TEXT,
    elapsed_ms INTEGER
);
CREATE INDEX provider_calls_analysis ON provider_calls(analysis_id);
CREATE INDEX provider_calls_investigation ON provider_calls(investigation_id);

CREATE TABLE budget_scopes (
    scope_key TEXT PRIMARY KEY,
    cap_microusd INTEGER NOT NULL,
    reserved_microusd INTEGER NOT NULL DEFAULT 0,
    charged_microusd INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER,
    updated_at TEXT NOT NULL,
    CHECK (reserved_microusd >= 0 AND charged_microusd >= 0)
);

CREATE TABLE exports (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    analysis_id TEXT NOT NULL REFERENCES analyses(id),
    status TEXT NOT NULL CHECK (status IN ('queued', 'building', 'sealed', 'failed', 'revoked')),
    rel_dir TEXT,
    archive_asset_id TEXT,
    head_digest TEXT,
    file_count INTEGER,
    bytes INTEGER,
    created_at TEXT NOT NULL,
    sealed_at TEXT
);

-- Trusted expected heads live here, outside every downloadable bundle.
CREATE TABLE trusted_heads (
    export_id TEXT PRIMARY KEY REFERENCES exports(id),
    head_digest TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    source TEXT NOT NULL
);

CREATE TABLE remote_files (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    clip_id TEXT,
    analysis_id TEXT,
    provider_file_id TEXT NOT NULL UNIQUE,
    purpose TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    delete_requested_at TEXT,
    deleted_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);

CREATE TABLE demos (
    id TEXT PRIMARY KEY,
    analysis_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    approval_note TEXT NOT NULL,
    published_at TEXT NOT NULL,
    rel_dir TEXT NOT NULL
);

CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
