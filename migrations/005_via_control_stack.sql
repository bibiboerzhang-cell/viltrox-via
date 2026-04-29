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
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    applied_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_pg_via_decision_session_created ON via_decision_ledger(session_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_decision_type_created ON via_decision_ledger(decision_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_outcome_session_created ON via_outcome_ledger(session_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_outcome_decision_created ON via_outcome_ledger(decision_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_proposal_policy_updated ON via_policy_proposals(policy_key, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_proposal_status_updated ON via_policy_proposals(status, updated_at DESC);
