CREATE TABLE IF NOT EXISTS via_reward_traces (
    id BIGSERIAL PRIMARY KEY,
    trace_id TEXT NOT NULL UNIQUE,
    session_key TEXT NOT NULL,
    decision_id TEXT DEFAULT '',
    user_id BIGINT NOT NULL DEFAULT 0,
    event_type TEXT NOT NULL,
    product_key TEXT DEFAULT '',
    event_value DOUBLE PRECISION NOT NULL DEFAULT 0,
    event_payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pg_via_reward_trace_session_created ON via_reward_traces(session_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_reward_trace_decision_created ON via_reward_traces(decision_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pg_via_reward_trace_event_created ON via_reward_traces(event_type, created_at DESC);
