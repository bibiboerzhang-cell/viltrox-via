CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name TEXT DEFAULT '',
    creator_code TEXT UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    role TEXT NOT NULL DEFAULT 'creator',
    points_balance INTEGER NOT NULL DEFAULT 0,
    points_pending INTEGER NOT NULL DEFAULT 0,
    points_total INTEGER NOT NULL DEFAULT 0,
    last_login TIMESTAMPTZ,
    note TEXT DEFAULT '',
    email_verified INTEGER NOT NULL DEFAULT 0,
    social_verified INTEGER NOT NULL DEFAULT 0,
    avatar_url TEXT DEFAULT '',
    bio TEXT DEFAULT '',
    signature TEXT DEFAULT '',
    tier_status TEXT NOT NULL DEFAULT 'pending',
    trust_score DOUBLE PRECISION NOT NULL DEFAULT 30.0,
    trust_updated_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS user_social_accounts (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    handle TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0,
    verified_at TIMESTAMPTZ,
    verify_code TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(platform, handle)
);

CREATE TABLE IF NOT EXISTS user_addresses (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    address1 TEXT DEFAULT '',
    address2 TEXT DEFAULT '',
    city TEXT DEFAULT '',
    state TEXT DEFAULT '',
    country TEXT DEFAULT 'US',
    postal_code TEXT DEFAULT '',
    is_default INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS email_tokens (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL DEFAULT 'verify_email',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS submissions (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    platform TEXT DEFAULT '',
    url TEXT DEFAULT '',
    extracted_handle TEXT DEFAULT '',
    title TEXT DEFAULT '',
    caption TEXT DEFAULT '',
    raw_text TEXT DEFAULT '',
    detection_status TEXT DEFAULT 'queued',
    job_status TEXT DEFAULT 'queued',
    product_series TEXT DEFAULT '',
    product_label TEXT DEFAULT '',
    content_types TEXT NOT NULL DEFAULT '[]',
    final_score INTEGER NOT NULL DEFAULT 0,
    creator_score INTEGER NOT NULL DEFAULT 0,
    overall_score INTEGER NOT NULL DEFAULT 0,
    risk_score INTEGER NOT NULL DEFAULT 0,
    views INTEGER NOT NULL DEFAULT 0,
    likes INTEGER NOT NULL DEFAULT 0,
    comments INTEGER NOT NULL DEFAULT 0,
    shares INTEGER NOT NULL DEFAULT 0,
    favorites INTEGER NOT NULL DEFAULT 0,
    recommendation TEXT DEFAULT '',
    memo TEXT DEFAULT '',
    evidence TEXT NOT NULL DEFAULT '[]',
    scraped_ok INTEGER NOT NULL DEFAULT 0,
    video_analysis TEXT NOT NULL DEFAULT '{}',
    video_path TEXT DEFAULT '',
    tech_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    marketing_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    content_genre TEXT DEFAULT '',
    percentile_tech DOUBLE PRECISION NOT NULL DEFAULT 0,
    percentile_mkt DOUBLE PRECISION NOT NULL DEFAULT 0,
    vertical_category TEXT DEFAULT '',
    vertical_tech_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    vertical_mkt_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    community_value DOUBLE PRECISION NOT NULL DEFAULT 0,
    product_showcase_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    brand_exposure_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    storytelling_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    tech_status TEXT DEFAULT '',
    logo_detected INTEGER NOT NULL DEFAULT 0,
    product_closeup_count INTEGER NOT NULL DEFAULT 0,
    points_awarded INTEGER NOT NULL DEFAULT 0,
    points_pending INTEGER NOT NULL DEFAULT 0,
    points_status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT DEFAULT '',
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    confirm_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS genre_benchmarks (
    genre TEXT PRIMARY KEY,
    sample_count INTEGER NOT NULL DEFAULT 0,
    p25_tech DOUBLE PRECISION NOT NULL DEFAULT 0,
    p50_tech DOUBLE PRECISION NOT NULL DEFAULT 0,
    p75_tech DOUBLE PRECISION NOT NULL DEFAULT 0,
    p90_tech DOUBLE PRECISION NOT NULL DEFAULT 0,
    p25_mkt DOUBLE PRECISION NOT NULL DEFAULT 0,
    p50_mkt DOUBLE PRECISION NOT NULL DEFAULT 0,
    p75_mkt DOUBLE PRECISION NOT NULL DEFAULT 0,
    p90_mkt DOUBLE PRECISION NOT NULL DEFAULT 0,
    avg_overall DOUBLE PRECISION NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS insights_cache (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS submission_assets (
    id BIGSERIAL PRIMARY KEY,
    submission_id BIGINT NOT NULL DEFAULT 0,
    asset_role TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    mime_type TEXT DEFAULT '',
    size_bytes BIGINT NOT NULL DEFAULT 0,
    duration_ms BIGINT NOT NULL DEFAULT 0,
    width INTEGER NOT NULL DEFAULT 0,
    height INTEGER NOT NULL DEFAULT 0,
    checksum TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS asset_fingerprints (
    id BIGSERIAL PRIMARY KEY,
    asset_id BIGINT NOT NULL REFERENCES submission_assets(id) ON DELETE CASCADE,
    fingerprint_type TEXT NOT NULL,
    frame_slot TEXT DEFAULT '',
    frame_index INTEGER NOT NULL DEFAULT 0,
    fingerprint_value TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS verifications (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    platform TEXT NOT NULL,
    handle TEXT NOT NULL,
    code TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    approved_at TIMESTAMPTZ,
    note TEXT DEFAULT '',
    profile_url TEXT DEFAULT '',
    baseline_username TEXT DEFAULT '',
    baseline_followers INTEGER NOT NULL DEFAULT 0,
    baseline_avatar_url TEXT DEFAULT '',
    baseline_bio TEXT DEFAULT '',
    baseline_data_json TEXT NOT NULL DEFAULT '{}',
    generated_comment TEXT DEFAULT '',
    posted_at TIMESTAMPTZ,
    match_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    scan_count INTEGER NOT NULL DEFAULT 0,
    last_scanned_at TIMESTAMPTZ,
    comment_id TEXT DEFAULT '',
    comment_username TEXT DEFAULT '',
    comment_text TEXT DEFAULT '',
    comment_video_url TEXT DEFAULT '',
    expires_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS points_log (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    submission_id BIGINT REFERENCES submissions(id) ON DELETE SET NULL,
    delta INTEGER NOT NULL,
    reason TEXT DEFAULT '',
    balance_after INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS reward_catalog (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    category TEXT NOT NULL,
    points_cost INTEGER NOT NULL,
    meta_label TEXT DEFAULT '',
    image_url TEXT DEFAULT '',
    stock INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 100,
    status TEXT NOT NULL DEFAULT 'draft',
    published_at TIMESTAMPTZ,
    published_by BIGINT REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS redemptions (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reward_id BIGINT REFERENCES reward_catalog(id) ON DELETE SET NULL,
    item_name TEXT DEFAULT '',
    item_category TEXT DEFAULT '',
    points_cost INTEGER NOT NULL DEFAULT 0,
    address_id BIGINT REFERENCES user_addresses(id) ON DELETE SET NULL,
    address_snapshot TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    tracking_number TEXT DEFAULT '',
    admin_note TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS persistent_cache (
    cache_key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL DEFAULT '{}',
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rate_limit_log (
    id BIGSERIAL PRIMARY KEY,
    bucket TEXT NOT NULL,
    client_id TEXT NOT NULL,
    blocked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bh_products (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    price DOUBLE PRECISION NOT NULL DEFAULT 0,
    rating DOUBLE PRECISION NOT NULL DEFAULT 0,
    review_count INTEGER NOT NULL DEFAULT 0,
    url TEXT DEFAULT '',
    image_url TEXT DEFAULT '',
    in_stock INTEGER NOT NULL DEFAULT 1,
    sku TEXT DEFAULT '',
    scraped_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    snapshot_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw_json TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS platform_ingest_events (
    id BIGSERIAL PRIMARY KEY,
    dedupe_key TEXT NOT NULL UNIQUE,
    source_platform TEXT NOT NULL,
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    external_id TEXT DEFAULT '',
    creator_handle TEXT DEFAULT '',
    region_code TEXT DEFAULT '',
    ingest_status TEXT NOT NULL DEFAULT 'queued',
    payload_json TEXT NOT NULL DEFAULT '{}',
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    error_message TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS creator_memory_entries (
    id BIGSERIAL PRIMARY KEY,
    memory_key TEXT NOT NULL UNIQUE,
    user_id BIGINT NOT NULL DEFAULT 0,
    creator_handle TEXT DEFAULT '',
    memory_kind TEXT NOT NULL,
    fact_key TEXT NOT NULL,
    fact_value_json TEXT NOT NULL DEFAULT '{}',
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    source_ref TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS market_observations (
    id BIGSERIAL PRIMARY KEY,
    observation_key TEXT NOT NULL UNIQUE,
    source_platform TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    observation_type TEXT NOT NULL,
    summary TEXT DEFAULT '',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    region_code TEXT DEFAULT '',
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS viltrox_matrix_accounts (
    id BIGSERIAL PRIMARY KEY,
    platform TEXT NOT NULL,
    handle TEXT NOT NULL,
    name TEXT NOT NULL,
    source_key TEXT NOT NULL DEFAULT 'official_matrix',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(platform, handle)
);

CREATE TABLE IF NOT EXISTS viltrox_matrix_scan_runs (
    id BIGSERIAL PRIMARY KEY,
    run_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'completed',
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    total_accounts INTEGER NOT NULL DEFAULT 0,
    scanned_accounts INTEGER NOT NULL DEFAULT 0,
    total_posts INTEGER NOT NULL DEFAULT 0,
    total_views INTEGER NOT NULL DEFAULT 0,
    total_likes INTEGER NOT NULL DEFAULT 0,
    total_comments INTEGER NOT NULL DEFAULT 0,
    aggregate_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS viltrox_matrix_scan_accounts (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES viltrox_matrix_scan_runs(id) ON DELETE CASCADE,
    account_id BIGINT NOT NULL REFERENCES viltrox_matrix_accounts(id) ON DELETE CASCADE,
    total_posts INTEGER NOT NULL DEFAULT 0,
    total_views INTEGER NOT NULL DEFAULT 0,
    total_likes INTEGER NOT NULL DEFAULT 0,
    total_comments INTEGER NOT NULL DEFAULT 0,
    duration_sec DOUBLE PRECISION NOT NULL DEFAULT 0,
    error_message TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(run_id, account_id)
);

CREATE TABLE IF NOT EXISTS viltrox_matrix_scan_posts (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES viltrox_matrix_scan_runs(id) ON DELETE CASCADE,
    account_id BIGINT NOT NULL REFERENCES viltrox_matrix_accounts(id) ON DELETE CASCADE,
    title TEXT DEFAULT '',
    post_url TEXT DEFAULT '',
    thumbnail_url TEXT DEFAULT '',
    views INTEGER NOT NULL DEFAULT 0,
    likes INTEGER NOT NULL DEFAULT 0,
    comments INTEGER NOT NULL DEFAULT 0,
    shares INTEGER NOT NULL DEFAULT 0,
    published_at TIMESTAMPTZ,
    content_type TEXT DEFAULT '',
    raw_json TEXT DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS product_knowledge (
    product_key TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    family TEXT DEFAULT '',
    mount_type TEXT DEFAULT '',
    alias_terms_json TEXT NOT NULL DEFAULT '[]',
    feature_tags_json TEXT NOT NULL DEFAULT '[]',
    scene_tags_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'seed',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS product_visual_features (
    id BIGSERIAL PRIMARY KEY,
    product_key TEXT NOT NULL REFERENCES product_knowledge(product_key) ON DELETE CASCADE,
    asset_role TEXT DEFAULT '',
    storage_key TEXT DEFAULT '',
    feature_type TEXT NOT NULL,
    feature_vector_json TEXT NOT NULL DEFAULT '{}',
    detector_version TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS region_market_facts (
    id BIGSERIAL PRIMARY KEY,
    fact_key TEXT NOT NULL UNIQUE,
    region_code TEXT NOT NULL,
    region_level TEXT NOT NULL DEFAULT 'country',
    fact_type TEXT NOT NULL,
    fact_value_json TEXT NOT NULL DEFAULT '{}',
    source_platform TEXT DEFAULT '',
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS feedback_events (
    id BIGSERIAL PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id TEXT DEFAULT '',
    event_type TEXT NOT NULL,
    actor_role TEXT DEFAULT '',
    user_id BIGINT NOT NULL DEFAULT 0,
    submission_id BIGINT NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS trust_events (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    score_delta DOUBLE PRECISION NOT NULL DEFAULT 0,
    new_total DOUBLE PRECISION NOT NULL DEFAULT 0,
    context_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS via_personas (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL DEFAULT 0,
    persona_key TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL DEFAULT 'Via',
    archetype TEXT DEFAULT 'brand_avatar',
    temperament TEXT DEFAULT 'balanced',
    talk_style TEXT DEFAULT 'warm',
    talkativeness DOUBLE PRECISION NOT NULL DEFAULT 0.55,
    curiosity DOUBLE PRECISION NOT NULL DEFAULT 0.7,
    outfit_code TEXT DEFAULT 'viltrox_core_black',
    accessory_code TEXT DEFAULT '',
    profile_json TEXT NOT NULL DEFAULT '{}',
    memory_policy_json TEXT NOT NULL DEFAULT '{}',
    affinity_points INTEGER NOT NULL DEFAULT 0,
    wardrobe_points INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS via_sessions (
    id BIGSERIAL PRIMARY KEY,
    session_key TEXT NOT NULL UNIQUE,
    user_id BIGINT NOT NULL DEFAULT 0,
    persona_id BIGINT NOT NULL DEFAULT 0,
    signed_device_id TEXT DEFAULT '',
    client_fingerprint TEXT DEFAULT '',
    ip_hash TEXT DEFAULT '',
    current_surface TEXT DEFAULT 'upload',
    base_model TEXT DEFAULT '',
    session_state_json TEXT NOT NULL DEFAULT '{}',
    last_event_id TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS via_memory_refs (
    id BIGSERIAL PRIMARY KEY,
    session_id BIGINT NOT NULL REFERENCES via_sessions(id) ON DELETE CASCADE,
    memory_kind TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    memory_key TEXT DEFAULT '',
    weight DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS via_decision_ledger (
    id BIGSERIAL PRIMARY KEY,
    decision_id TEXT NOT NULL UNIQUE,
    session_key TEXT NOT NULL,
    session_id BIGINT NOT NULL DEFAULT 0,
    user_id BIGINT NOT NULL DEFAULT 0,
    persona_id BIGINT NOT NULL DEFAULT 0,
    decision_type TEXT NOT NULL,
    trigger_type TEXT DEFAULT '',
    trigger_payload_json TEXT NOT NULL DEFAULT '{}',
    state_snapshot_json TEXT NOT NULL DEFAULT '{}',
    candidates_json TEXT NOT NULL DEFAULT '[]',
    chosen_action_json TEXT NOT NULL DEFAULT '{}',
    policy_key TEXT DEFAULT '',
    policy_version TEXT DEFAULT '',
    context_refs_json TEXT NOT NULL DEFAULT '[]',
    latency_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
    cost_estimate DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS via_outcome_ledger (
    id BIGSERIAL PRIMARY KEY,
    outcome_id TEXT NOT NULL UNIQUE,
    decision_id TEXT NOT NULL,
    session_key TEXT NOT NULL,
    accepted INTEGER NOT NULL DEFAULT 0,
    followup_depth INTEGER NOT NULL DEFAULT 0,
    rephrase_needed INTEGER NOT NULL DEFAULT 0,
    clicked_product INTEGER NOT NULL DEFAULT 0,
    added_to_cart INTEGER NOT NULL DEFAULT 0,
    purchased INTEGER NOT NULL DEFAULT 0,
    thumb_feedback INTEGER NOT NULL DEFAULT 0,
    abuse_flag INTEGER NOT NULL DEFAULT 0,
    reward_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    outcome_payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS via_reward_traces (
    id BIGSERIAL PRIMARY KEY,
    trace_id TEXT NOT NULL UNIQUE,
    session_key TEXT NOT NULL,
    decision_id TEXT DEFAULT '',
    user_id BIGINT NOT NULL DEFAULT 0,
    event_type TEXT NOT NULL,
    surface TEXT DEFAULT '',
    source TEXT DEFAULT '',
    origin TEXT DEFAULT '',
    product_key TEXT DEFAULT '',
    event_value DOUBLE PRECISION NOT NULL DEFAULT 0,
    idempotency_key TEXT DEFAULT '',
    event_payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS via_retrieval_evidence (
    id BIGSERIAL PRIMARY KEY,
    evidence_id TEXT NOT NULL UNIQUE,
    session_key TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    policy_key TEXT DEFAULT '',
    policy_version TEXT DEFAULT '',
    retrieval_mode TEXT DEFAULT '',
    candidate_sources_json TEXT NOT NULL DEFAULT '[]',
    selected_sources_json TEXT NOT NULL DEFAULT '[]',
    vector_hit_count INTEGER NOT NULL DEFAULT 0,
    bundle_hit_count INTEGER NOT NULL DEFAULT 0,
    seed_hit_count INTEGER NOT NULL DEFAULT 0,
    vector_limit INTEGER NOT NULL DEFAULT 0,
    top_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    avg_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    score_spread DOUBLE PRECISION NOT NULL DEFAULT 0,
    rerank_applied INTEGER NOT NULL DEFAULT 0,
    rerank_summary_json TEXT NOT NULL DEFAULT '{}',
    evidence_payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS via_policy_proposals (
    id BIGSERIAL PRIMARY KEY,
    proposal_key TEXT NOT NULL UNIQUE,
    proposal_type TEXT NOT NULL,
    policy_key TEXT NOT NULL,
    status TEXT DEFAULT 'proposed',
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
    impact_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    proposal_json TEXT NOT NULL DEFAULT '{}',
    window_days INTEGER NOT NULL DEFAULT 0,
    evaluator_version TEXT DEFAULT '',
    reviewed_by TEXT DEFAULT '',
    review_note TEXT DEFAULT '',
    reviewed_at TIMESTAMPTZ,
    applied_version_key TEXT DEFAULT '',
    applied_by TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    applied_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS via_policy_versions (
    id BIGSERIAL PRIMARY KEY,
    version_key TEXT NOT NULL UNIQUE,
    policy_key TEXT NOT NULL,
    version_label TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'live',
    source_proposal_key TEXT DEFAULT '',
    config_json TEXT NOT NULL DEFAULT '{}',
    approved_by TEXT DEFAULT '',
    approved_at TIMESTAMPTZ,
    applied_by TEXT DEFAULT '',
    applied_at TIMESTAMPTZ,
    review_note TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS via_rollout_alerts (
    id BIGSERIAL PRIMARY KEY,
    alert_key TEXT NOT NULL UNIQUE,
    policy_key TEXT NOT NULL,
    version_key TEXT NOT NULL,
    version_label TEXT DEFAULT '',
    alert_type TEXT NOT NULL,
    severity TEXT DEFAULT 'medium',
    status TEXT DEFAULT 'open',
    recommendation TEXT DEFAULT '',
    reason_text TEXT DEFAULT '',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    observed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS via_routing_provider_stats (
    id BIGSERIAL PRIMARY KEY,
    bucket_key TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT 'dialogue_generation',
    provider TEXT NOT NULL,
    exposure_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    reward_sum DOUBLE PRECISION NOT NULL DEFAULT 0,
    guard_fail_count INTEGER NOT NULL DEFAULT 0,
    avg_latency_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
    avg_cost_estimate DOUBLE PRECISION NOT NULL DEFAULT 0,
    last_outcome_at TIMESTAMPTZ,
    metrics_json TEXT NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(bucket_key, target, provider)
);

CREATE TABLE IF NOT EXISTS via_memory_retention_stats (
    id BIGSERIAL PRIMARY KEY,
    retention_key TEXT NOT NULL UNIQUE,
    user_id BIGINT DEFAULT 0,
    session_key TEXT DEFAULT '',
    memory_tier TEXT DEFAULT '',
    memory_kind TEXT DEFAULT '',
    fact_key TEXT DEFAULT '',
    source_ref TEXT DEFAULT '',
    confirmed_hits INTEGER NOT NULL DEFAULT 0,
    reinforcement_count INTEGER NOT NULL DEFAULT 0,
    cumulative_reward DOUBLE PRECISION NOT NULL DEFAULT 0,
    last_hit_at TIMESTAMPTZ,
    last_promoted_at TIMESTAMPTZ,
    decay_state TEXT DEFAULT 'fresh',
    status TEXT DEFAULT 'active',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS schools (
    id BIGSERIAL PRIMARY KEY,
    school_id TEXT NOT NULL UNIQUE,
    school_code TEXT NOT NULL,
    school_name TEXT NOT NULL,
    school_name_native TEXT DEFAULT '',
    country TEXT DEFAULT '',
    region TEXT DEFAULT '',
    school_type TEXT DEFAULT 'film',
    tier TEXT DEFAULT 'standard',
    partnership_status TEXT DEFAULT 'pilot',
    visual_theme_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS student_qr_codes (
    id BIGSERIAL PRIMARY KEY,
    qr_id TEXT NOT NULL UNIQUE,
    school_id TEXT NOT NULL,
    issued_batch TEXT DEFAULT '',
    display_serial TEXT DEFAULT '',
    claim_token TEXT NOT NULL,
    claim_signature TEXT NOT NULL,
    claim_url TEXT DEFAULT '',
    qr_code_url TEXT DEFAULT '',
    card_image_url TEXT DEFAULT '',
    manifest_url TEXT DEFAULT '',
    status TEXT DEFAULT 'issued',
    roster_mode TEXT DEFAULT 'anonymous',
    bound_user_id BIGINT NOT NULL DEFAULT 0,
    bound_at TIMESTAMPTZ,
    issued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    revoked_reason TEXT DEFAULT '',
    prefilled_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS student_verifications (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    school_id TEXT NOT NULL,
    student_id_code TEXT NOT NULL,
    verification_method TEXT DEFAULT 'qr_scan',
    verification_proof_json TEXT NOT NULL DEFAULT '{}',
    status TEXT DEFAULT 'active',
    commission_rate_override DOUBLE PRECISION NOT NULL DEFAULT 0.10,
    verified_by TEXT DEFAULT 'system_qr',
    verified_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, school_id)
);

CREATE TABLE IF NOT EXISTS student_scan_events (
    id BIGSERIAL PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    qr_id TEXT DEFAULT '',
    user_id BIGINT NOT NULL DEFAULT 0,
    school_id TEXT DEFAULT '',
    event_type TEXT NOT NULL,
    location TEXT DEFAULT '',
    event_payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS student_identity_audit_log (
    id BIGSERIAL PRIMARY KEY,
    audit_key TEXT NOT NULL UNIQUE,
    qr_id TEXT DEFAULT '',
    user_id BIGINT NOT NULL DEFAULT 0,
    school_id TEXT DEFAULT '',
    audit_type TEXT NOT NULL,
    actor TEXT DEFAULT '',
    reason TEXT DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_assets_checksum ON submission_assets(checksum);
CREATE INDEX IF NOT EXISTS idx_asset_fp_asset_type ON asset_fingerprints(asset_id, fingerprint_type);
CREATE INDEX IF NOT EXISTS idx_asset_fp_value_type ON asset_fingerprints(fingerprint_value, fingerprint_type);
CREATE INDEX IF NOT EXISTS idx_pg_submissions_user_created ON submissions(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_pg_submissions_status_created ON submissions(detection_status, id DESC);
CREATE INDEX IF NOT EXISTS idx_pg_submissions_job_created ON submissions(job_status, id DESC);
CREATE INDEX IF NOT EXISTS idx_pg_submissions_platform_created ON submissions(platform, id DESC);
CREATE INDEX IF NOT EXISTS idx_pg_submissions_handle_created ON submissions(extracted_handle, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_submission_assets_submission ON submission_assets(submission_id);
CREATE INDEX IF NOT EXISTS idx_pg_verifications_status_created ON verifications(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_points_log_user_created ON points_log(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_pg_redemptions_user_created ON redemptions(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_pg_ingest_platform_status ON platform_ingest_events(source_platform, ingest_status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_ingest_entity_external ON platform_ingest_events(entity_type, external_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_creator_memory_handle_kind ON creator_memory_entries(creator_handle, memory_kind, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_market_obs_platform_subject ON market_observations(source_platform, subject_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_product_visual_product_type ON product_visual_features(product_key, feature_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_region_facts_region_type ON region_market_facts(region_code, fact_type, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_feedback_submission_created ON feedback_events(submission_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_trust_events_user_created ON trust_events(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_trust_events_event_created ON trust_events(event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_vx_accounts_platform_active ON viltrox_matrix_accounts(platform, is_active, name);
CREATE INDEX IF NOT EXISTS idx_pg_vx_runs_completed ON viltrox_matrix_scan_runs(completed_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_pg_vx_scan_accounts_run_account ON viltrox_matrix_scan_accounts(run_id, account_id);
CREATE INDEX IF NOT EXISTS idx_pg_vx_scan_posts_run_published ON viltrox_matrix_scan_posts(run_id, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_vx_scan_posts_account_published ON viltrox_matrix_scan_posts(account_id, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_personas_user_updated ON via_personas(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_sessions_user_updated ON via_sessions(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_sessions_persona_updated ON via_sessions(persona_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_sessions_device_updated ON via_sessions(signed_device_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_memory_session_created ON via_memory_refs(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_memory_kind_created ON via_memory_refs(memory_kind, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_decision_session_created ON via_decision_ledger(session_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_decision_type_created ON via_decision_ledger(decision_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_outcome_session_created ON via_outcome_ledger(session_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_outcome_decision_created ON via_outcome_ledger(decision_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_reward_trace_session_created ON via_reward_traces(session_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_reward_trace_decision_created ON via_reward_traces(decision_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_reward_trace_event_created ON via_reward_traces(event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_reward_trace_idempotency ON via_reward_traces(idempotency_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_retrieval_evidence_decision_created ON via_retrieval_evidence(decision_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_retrieval_evidence_policy_created ON via_retrieval_evidence(policy_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_proposal_policy_updated ON via_policy_proposals(policy_key, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_proposal_status_updated ON via_policy_proposals(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_policy_versions_policy_created ON via_policy_versions(policy_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_policy_versions_status_policy ON via_policy_versions(status, policy_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_rollout_alert_policy_created ON via_rollout_alerts(policy_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_rollout_alert_version_created ON via_rollout_alerts(version_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_routing_provider_bucket_updated ON via_routing_provider_stats(bucket_key, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_routing_provider_target_updated ON via_routing_provider_stats(target, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_memory_retention_tier_updated ON via_memory_retention_stats(memory_tier, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_memory_retention_source_updated ON via_memory_retention_stats(source_ref, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_memory_retention_user_updated ON via_memory_retention_stats(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_schools_code_name ON schools(school_code, school_name);
CREATE INDEX IF NOT EXISTS idx_pg_student_qr_school_status ON student_qr_codes(school_id, status, issued_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_student_qr_batch_serial ON student_qr_codes(issued_batch, display_serial);
CREATE INDEX IF NOT EXISTS idx_pg_student_qr_user_bound ON student_qr_codes(bound_user_id, bound_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_student_verifications_user_status ON student_verifications(user_id, status, verified_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_student_verifications_school_status ON student_verifications(school_id, status, verified_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_student_scan_events_user_created ON student_scan_events(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_student_scan_events_qr_created ON student_scan_events(qr_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_student_audit_qr_created ON student_identity_audit_log(qr_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_student_audit_user_created ON student_identity_audit_log(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_student_audit_school_created ON student_identity_audit_log(school_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_cache_expires ON persistent_cache(expires_at);
CREATE INDEX IF NOT EXISTS idx_pg_rate_limit_bucket_client ON rate_limit_log(bucket, client_id, blocked_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_bh_snapshot_title ON bh_products(snapshot_at DESC, title);
