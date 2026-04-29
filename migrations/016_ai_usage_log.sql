-- AI/provider usage and health visibility for SystemTab.

CREATE TABLE IF NOT EXISTS ai_usage_log (
    id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT DEFAULT '',
    request_id TEXT DEFAULT '',
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_usd NUMERIC(12, 6) NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    success INTEGER NOT NULL DEFAULT 1,
    error_code TEXT DEFAULT '',
    called_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    triggered_by TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_ai_usage_provider_called
    ON ai_usage_log(provider, called_at DESC);

CREATE INDEX IF NOT EXISTS idx_ai_usage_triggered
    ON ai_usage_log(triggered_by, called_at DESC);

CREATE TABLE IF NOT EXISTS provider_status (
    provider TEXT PRIMARY KEY,
    latest_status TEXT NOT NULL DEFAULT 'unknown',
    last_ok_at TIMESTAMPTZ,
    last_error TEXT DEFAULT '',
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    alert_sent_at TIMESTAMPTZ,
    quota_json TEXT NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO provider_status (provider, latest_status)
VALUES
    ('anthropic', 'unknown'),
    ('openai', 'unknown'),
    ('google', 'unknown'),
    ('apify', 'unknown'),
    ('resend', 'unknown')
ON CONFLICT(provider) DO NOTHING;
