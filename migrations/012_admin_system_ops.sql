ALTER TABLE users ADD COLUMN IF NOT EXISTS trust_status TEXT NOT NULL DEFAULT 'normal';
ALTER TABLE users ADD COLUMN IF NOT EXISTS violation_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS blocked_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS blocked_reason TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS flagged_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS flagged_reason TEXT;

CREATE TABLE IF NOT EXISTS trust_rules (
    id BIGSERIAL PRIMARY KEY,
    event_kind TEXT NOT NULL UNIQUE,
    delta INTEGER NOT NULL DEFAULT 0,
    description TEXT DEFAULT '',
    is_positive INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by BIGINT REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS staff (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    role TEXT NOT NULL DEFAULT 'readonly',
    permissions_json TEXT NOT NULL DEFAULT '{}',
    mfa_enabled INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    invited_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
    invited_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    accepted_at TIMESTAMPTZ,
    last_active_at TIMESTAMPTZ,
    suspended_at TIMESTAMPTZ,
    suspended_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_staff_user ON staff(user_id);
CREATE INDEX IF NOT EXISTS idx_staff_active ON staff(active, role);

CREATE TABLE IF NOT EXISTS admin_audit_log (
    id BIGSERIAL PRIMARY KEY,
    actor_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    actor_name TEXT DEFAULT '',
    action TEXT NOT NULL,
    target_type TEXT DEFAULT '',
    target_id TEXT DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}',
    ip_address TEXT DEFAULT '',
    user_agent TEXT DEFAULT '',
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON admin_audit_log(actor_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action ON admin_audit_log(action, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_target ON admin_audit_log(target_type, target_id);

CREATE TABLE IF NOT EXISTS api_tokens (
    id BIGSERIAL PRIMARY KEY,
    token_prefix TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'readonly',
    created_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMPTZ,
    last_used_ip TEXT,
    expires_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    revoked_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_api_tokens_active ON api_tokens(active, revoked_at);
CREATE INDEX IF NOT EXISTS idx_api_tokens_prefix ON api_tokens(token_prefix);

CREATE TABLE IF NOT EXISTS integrations (
    id BIGSERIAL PRIMARY KEY,
    service_name TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    purpose TEXT DEFAULT '',
    config_env_prefix TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'not_configured',
    last_health_check TIMESTAMPTZ,
    last_health_status TEXT,
    last_error TEXT,
    notes TEXT,
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS integration_metrics (
    id BIGSERIAL PRIMARY KEY,
    integration_id BIGINT REFERENCES integrations(id) ON DELETE CASCADE,
    bucket_start TIMESTAMPTZ NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    latency_p50_ms INTEGER,
    latency_p95_ms INTEGER,
    cost_cents INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_int_metrics ON integration_metrics(integration_id, bucket_start DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_int_metrics_unique_bucket ON integration_metrics(integration_id, bucket_start);

INSERT INTO trust_rules (event_kind, delta, description, is_positive) VALUES
    ('submission_approved',          5,   'AI-approved or admin-approved submission', 1),
    ('verification_confirmed',       10,  'Email/handle verified', 1),
    ('shopify_order_attributed',     8,   'Real commerce outcome', 1),
    ('first_sale_via_student_qr',    15,  'Student first activation', 1),
    ('duplicate_submission',         -5,  'Same video submitted multiple times', 0),
    ('suspected_fake_engagement',    -15, 'Views/likes pattern inconsistent', 0),
    ('bot_pattern_detected',         -30, 'Behavior matches known bot signals', 0),
    ('third_violation_blocked',      -50, 'Auto-block after 3 violations', 0)
ON CONFLICT (event_kind) DO NOTHING;

INSERT INTO integrations (service_name, category, purpose, config_env_prefix, status) VALUES
    ('anthropic',   'ai',       'Claude Opus/Sonnet/Haiku', 'ANTHROPIC_',   'ok'),
    ('openai',      'ai',       'GPT-4o-mini pre-filter',   'OPENAI_',      'ok'),
    ('google',      'ai',       'Gemini 2.5 Flash',         'GOOGLE_',      'ok'),
    ('shopify',     'commerce', 'Order webhooks',           'SHOPIFY_',     'ok'),
    ('paypal',      'commerce', 'Creator payouts',          'PAYPAL_',      'not_configured'),
    ('stripe',      'commerce', 'Bank transfers',           'STRIPE_',      'not_configured'),
    ('apify',       'data',     'B&H scraper',              'APIFY_',       'ok'),
    ('qdrant',      'data',     'Vector store',             'QDRANT_',      'ok'),
    ('redis',       'data',     'Cache',                    'REDIS_',       'ok'),
    ('resend',      'email',    'Transactional email',      'RESEND_',      'warn'),
    ('meta',        'email',    'Instagram webhooks',       'META_',        'not_configured'),
    ('tiktok',      'email',    'TikTok API',               'TIKTOK_',      'not_configured'),
    ('youtube',     'email',    'YouTube Data API',         'YOUTUBE_',     'ok'),
    ('sentry',      'data',     'Error tracking',           'SENTRY_',      'not_configured')
ON CONFLICT (service_name) DO NOTHING;
