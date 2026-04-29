ALTER TABLE via_reward_traces ADD COLUMN IF NOT EXISTS surface TEXT DEFAULT '';
ALTER TABLE via_reward_traces ADD COLUMN IF NOT EXISTS source TEXT DEFAULT '';
ALTER TABLE via_reward_traces ADD COLUMN IF NOT EXISTS origin TEXT DEFAULT '';
ALTER TABLE via_reward_traces ADD COLUMN IF NOT EXISTS idempotency_key TEXT DEFAULT '';

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

CREATE INDEX IF NOT EXISTS idx_pg_via_reward_trace_idempotency ON via_reward_traces(idempotency_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_retrieval_evidence_decision_created ON via_retrieval_evidence(decision_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_retrieval_evidence_policy_created ON via_retrieval_evidence(policy_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_rollout_alert_policy_created ON via_rollout_alerts(policy_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_rollout_alert_version_created ON via_rollout_alerts(version_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_routing_provider_bucket_updated ON via_routing_provider_stats(bucket_key, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_routing_provider_target_updated ON via_routing_provider_stats(target, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_memory_retention_tier_updated ON via_memory_retention_stats(memory_tier, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_memory_retention_source_updated ON via_memory_retention_stats(source_ref, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_memory_retention_user_updated ON via_memory_retention_stats(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_student_audit_qr_created ON student_identity_audit_log(qr_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_student_audit_user_created ON student_identity_audit_log(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_student_audit_school_created ON student_identity_audit_log(school_id, created_at DESC);
