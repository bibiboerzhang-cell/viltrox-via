-- ============================================================================
-- V-OS Admin V5 — Consolidated SQL migration (batches 2-5)
-- ============================================================================
-- Run order: idempotent (CREATE TABLE IF NOT EXISTS). Safe to re-run.
-- Target: SQLite (WAL mode) — adjust ALTER ADD COLUMN for Postgres if migrating.
-- Reference batches:
--   B2: orders / payout_cycles / payouts / attribution_clicks
--   B3: ai_insights / market_observations / genre_benchmarks / correlation_cache
--   B4: via_policy_versions / via_policy_events / via_eval_runs / via_personas / via_proposals / via_session_meta
--   B5: trust_events / trust_rules / staff / admin_audit_log / api_tokens / integrations / integration_metrics
--       + users compat ALTER (trust_score if missing as REAL DEFAULT 30.0 /
--         trust_status / violation_count / flagged_at / blocked_at)
-- ============================================================================

-- =========================================================================
-- BATCH 2 — Commerce (orders, payouts, attribution)
-- =========================================================================

CREATE TABLE IF NOT EXISTS orders (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    external_order_id        TEXT UNIQUE NOT NULL,          -- Shopify "#VX2387"
    source_platform          TEXT NOT NULL,                 -- 'shopify'
    customer_email           TEXT,
    customer_country         TEXT,
    subtotal_cents           INTEGER NOT NULL,
    currency                 TEXT DEFAULT 'USD',
    items_json               TEXT,                          -- [{sku,name,qty,price_cents}]

    -- Attribution
    attribution_source       TEXT,                          -- creator_handle | student_id | 'direct'
    attribution_type         TEXT,                          -- 'creator' | 'student' | 'direct'
    attribution_user_id      INTEGER REFERENCES users(id),
    utm_source               TEXT,
    utm_medium               TEXT,
    utm_campaign             TEXT,
    commission_rate_bps      INTEGER,                       -- 800=8%, 1000=10%
    commission_cents         INTEGER DEFAULT 0,

    -- Status
    status                   TEXT NOT NULL DEFAULT 'paid',  -- 'paid'|'refunded'|'partial_refund'
    placed_at                TEXT NOT NULL,
    webhook_event_ids_json   TEXT,                          -- [fk to platform_ingest_events.id]
    raw_payload              TEXT,                          -- original webhook body

    -- Flagging
    flagged_reason           TEXT,                          -- 'fraud'|'bot'|'duplicate'|NULL
    flagged_by               INTEGER REFERENCES users(id),
    flagged_at               TEXT,

    created_at               TEXT DEFAULT (datetime('now')),
    updated_at               TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_attribution ON orders(attribution_source, placed_at);
CREATE INDEX IF NOT EXISTS idx_orders_status      ON orders(status, placed_at);
CREATE INDEX IF NOT EXISTS idx_orders_utm         ON orders(utm_source, utm_medium);
CREATE INDEX IF NOT EXISTS idx_orders_user        ON orders(attribution_user_id, placed_at);


CREATE TABLE IF NOT EXISTS payout_cycles (
    id                    TEXT PRIMARY KEY,                    -- 'apr-2026'
    label                 TEXT NOT NULL,                       -- 'Apr 2026'
    start_date            TEXT NOT NULL,
    end_date              TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'upcoming',    -- upcoming|active|processing|processed
    process_date          TEXT NOT NULL,                       -- scheduled process day
    processed_at          TEXT,
    processed_by          INTEGER REFERENCES users(id),
    total_approved_cents  INTEGER DEFAULT 0,
    total_paid_cents      INTEGER DEFAULT 0,
    creator_count         INTEGER DEFAULT 0,
    created_at            TEXT DEFAULT (datetime('now'))
);


CREATE TABLE IF NOT EXISTS payouts (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id           TEXT NOT NULL REFERENCES payout_cycles(id),
    user_id            INTEGER NOT NULL REFERENCES users(id),
    amount_cents       INTEGER NOT NULL,
    currency           TEXT DEFAULT 'USD',
    order_ids_json     TEXT,                                -- [order_id, ...] covered by this payout
    gmv_cents          INTEGER DEFAULT 0,                   -- GMV that drove this payout
    order_count        INTEGER DEFAULT 0,

    method             TEXT,                                -- 'paypal'|'bank'|'hold'
    method_details     TEXT,                                -- JSON (encrypted PII)

    status             TEXT NOT NULL DEFAULT 'pending',     -- pending|approved|held|paid|failed
    hold_reason        TEXT,

    approved_at        TEXT, approved_by INTEGER REFERENCES users(id),
    paid_at            TEXT, paid_tx_id TEXT,
    failed_at          TEXT, failed_reason TEXT,

    created_at         TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_payouts_cycle ON payouts(cycle_id, status);
CREATE INDEX IF NOT EXISTS idx_payouts_user  ON payouts(user_id, created_at);


CREATE TABLE IF NOT EXISTS attribution_clicks (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    ref_code                 TEXT NOT NULL,          -- creator handle or student id
    ref_type                 TEXT,                   -- 'creator' | 'student'
    utm_source               TEXT,
    utm_medium               TEXT,
    utm_campaign             TEXT,
    session_id               TEXT,
    user_agent               TEXT,
    ip_hash                  TEXT,
    country                  TEXT,
    landing_path             TEXT,
    converted_to_order_id    INTEGER REFERENCES orders(id),
    clicked_at               TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_clicks_ref    ON attribution_clicks(ref_code, clicked_at);
CREATE INDEX IF NOT EXISTS idx_clicks_sess   ON attribution_clicks(session_id);


CREATE TABLE IF NOT EXISTS payout_disputes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    payout_id        INTEGER REFERENCES payouts(id),
    user_id          INTEGER REFERENCES users(id),
    reason           TEXT NOT NULL,
    evidence_json    TEXT,
    status           TEXT DEFAULT 'open',     -- open|resolved_uphold|resolved_overturn
    resolved_by      INTEGER REFERENCES users(id),
    resolved_at      TEXT, resolution_note TEXT,
    created_at       TEXT DEFAULT (datetime('now'))
);


-- =========================================================================
-- BATCH 3 — Intelligence (AI insights, market observations, benchmarks)
-- =========================================================================

CREATE TABLE IF NOT EXISTS ai_insights (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    module            TEXT NOT NULL,            -- 'market'|'brand'|'analytics'
    category          TEXT NOT NULL,            -- 'gap'|'brand_analysis'|'cohort'|'cross_module'
    severity          TEXT,                     -- 'info'|'amber'|'red'|'green'
    insight_type      TEXT,                     -- 'opportunity'|'leading'|'risk'|'behind'
    title             TEXT NOT NULL,
    body              TEXT NOT NULL,
    source_data_ref   TEXT,                     -- JSON describing which data was analyzed
    model_used        TEXT,                     -- 'claude-opus-4-7'
    generated_at      TEXT NOT NULL,
    expires_at        TEXT,                     -- cache TTL
    dismissed_at      TEXT, dismissed_by INTEGER REFERENCES users(id),
    created_at        TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_insights_module ON ai_insights(module, generated_at DESC);


CREATE TABLE IF NOT EXISTS market_observations (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at            TEXT NOT NULL,
    source_url             TEXT,
    event_kind             TEXT NOT NULL,         -- 'launch'|'review'|'price'|'stock'|'news'
    event_title            TEXT NOT NULL,
    focal_range            TEXT,
    impact                 TEXT,                  -- 'positive'|'competitive'|'neutral'|'negative'
    related_product_ids    TEXT,                  -- JSON array
    extracted_by_model     TEXT,
    notes                  TEXT,
    created_at             TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_obs_observed ON market_observations(observed_at DESC);


CREATE TABLE IF NOT EXISTS genre_benchmarks (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    genre                   TEXT UNIQUE NOT NULL,   -- 'unboxing'|'review'|'cinematic'
    avg_score_target        REAL,
    pass_rate_target        REAL,
    characteristics_json    TEXT,
    sample_count            INTEGER,
    last_updated            TEXT DEFAULT (datetime('now'))
);


CREATE TABLE IF NOT EXISTS correlation_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_a        TEXT NOT NULL,
    metric_b        TEXT NOT NULL,
    r               REAL,
    slope           REAL,
    lag_days        INTEGER,
    sample_size     INTEGER,
    window_start    TEXT,
    window_end      TEXT,
    computed_at     TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_corr_metrics ON correlation_cache(metric_a, metric_b);


-- =========================================================================
-- BATCH 4 — VIA Brain (policies, proposals, evals, personas)
-- =========================================================================

CREATE TABLE IF NOT EXISTS via_policy_versions (
    version_key            TEXT PRIMARY KEY,                -- 'v2.14.3'
    status                 TEXT NOT NULL,                   -- live|shadow|historical|rolled-back
    policy_yaml            TEXT NOT NULL,
    notes                  TEXT,
    created_at             TEXT NOT NULL,
    promoted_at            TEXT, promoted_by INTEGER REFERENCES users(id),
    promoted_by_system     BOOLEAN DEFAULT 0,
    rolled_back_at         TEXT, rolled_back_reason TEXT,
    current_rollout_pct    INTEGER DEFAULT 0,
    sessions_served        INTEGER DEFAULT 0
);


CREATE TABLE IF NOT EXISTS via_policy_events (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    version_key          TEXT REFERENCES via_policy_versions(version_key),
    event_type           TEXT,                -- created|staged|promoted|advanced|rolled_back
    from_pct             INTEGER,
    to_pct               INTEGER,
    actor_id             INTEGER REFERENCES users(id),
    reason               TEXT,
    metrics_snapshot     TEXT,                -- JSON
    occurred_at          TEXT DEFAULT (datetime('now'))
);


CREATE TABLE IF NOT EXISTS via_eval_runs (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT NOT NULL,
    kind                    TEXT NOT NULL,               -- scheduled|experiment|targeted|release
    sample_count            INTEGER,
    passed                  BOOLEAN,
    score                   TEXT,                        -- '8.4/10' | 'v2.15 wins'
    slices_json             TEXT,
    judge_rationale         TEXT,
    failed_examples_json    TEXT,
    policy_version_key      TEXT REFERENCES via_policy_versions(version_key),
    ran_at                  TEXT DEFAULT (datetime('now')),
    duration_seconds        INTEGER
);
CREATE INDEX IF NOT EXISTS idx_evals_ran_at ON via_eval_runs(ran_at DESC);


CREATE TABLE IF NOT EXISTS via_personas (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name               TEXT UNIQUE NOT NULL,
    use_case           TEXT,
    tone_descriptor    TEXT,
    system_prompt      TEXT NOT NULL,
    model_preferred    TEXT NOT NULL,                    -- 'claude-opus-4-7' etc
    active             BOOLEAN DEFAULT 1,
    created_at         TEXT DEFAULT (datetime('now')),
    updated_at         TEXT
);


CREATE TABLE IF NOT EXISTS via_proposals (
    id                    TEXT PRIMARY KEY,              -- 'prop_1041'
    kind                  TEXT NOT NULL,                 -- routing|prompt|memory|persona
    title                 TEXT NOT NULL,
    subtitle              TEXT,
    evidence              TEXT,
    predicted_impact      TEXT,
    impact_kind           TEXT,                          -- green|amber|red
    sample_count          INTEGER,
    is_breaking           BOOLEAN DEFAULT 0,
    stage                 TEXT NOT NULL DEFAULT 'pending',  -- pending|staged|approved|rejected|applied
    decision_reason       TEXT,
    decided_by            INTEGER REFERENCES users(id),
    generated_at          TEXT,
    decided_at            TEXT,
    preview_diff_yaml     TEXT
);
CREATE INDEX IF NOT EXISTS idx_prop_stage ON via_proposals(stage, generated_at DESC);


CREATE TABLE IF NOT EXISTS via_session_meta (
    session_key          TEXT PRIMARY KEY,
    user_handle          TEXT,
    user_id              INTEGER REFERENCES users(id),
    persona_id           INTEGER REFERENCES via_personas(id),
    model_used           TEXT,
    status               TEXT,                          -- active|resolved|abandoned|flagged
    turn_count           INTEGER DEFAULT 0,
    started_at           TEXT,
    last_activity_at     TEXT,
    ended_at             TEXT,
    flagged_reason       TEXT,
    flagged_by           INTEGER REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_via_sess_status ON via_session_meta(status, last_activity_at DESC);


-- =========================================================================
-- BATCH 5 — System (trust, staff, audit, api tokens, integrations)
-- =========================================================================

-- Extend users table with trust fields (each ADD wrapped so re-runs are OK on SQLite)
-- NOTE: SQLite doesn't support "ADD COLUMN IF NOT EXISTS" — runner must catch OperationalError
-- if re-executed. Python migration below handles this.
-- Reference columns:
--   trust_score REAL DEFAULT 30.0  (only added if missing on older DBs)
--   trust_status TEXT DEFAULT 'normal'
--   violation_count INTEGER DEFAULT 0
--   blocked_at TEXT / blocked_reason TEXT
--   flagged_at TEXT / flagged_reason TEXT


CREATE TABLE IF NOT EXISTS trust_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id),
    event_kind      TEXT NOT NULL,            -- 'submission_approved' | 'duplicate_submission' | ...
    delta           INTEGER NOT NULL,
    score_before    INTEGER,
    score_after     INTEGER,
    actor_type      TEXT,                     -- 'system' | 'admin' | 'ai'
    actor_id        INTEGER REFERENCES users(id),
    metadata_json   TEXT,
    occurred_at     TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_trust_events_user ON trust_events(user_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_trust_events_kind ON trust_events(event_kind, occurred_at DESC);


CREATE TABLE IF NOT EXISTS trust_rules (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    event_kind     TEXT UNIQUE NOT NULL,
    delta          INTEGER NOT NULL,
    description    TEXT,
    is_positive    BOOLEAN,
    enabled        BOOLEAN DEFAULT 1,
    updated_at     TEXT DEFAULT (datetime('now')),
    updated_by     INTEGER REFERENCES users(id)
);


CREATE TABLE IF NOT EXISTS staff (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                  INTEGER UNIQUE REFERENCES users(id),
    role                     TEXT NOT NULL,                   -- admin|operations|analyst|readonly
    permissions_json         TEXT,                            -- override base role perms
    mfa_enabled              BOOLEAN DEFAULT 0,
    mfa_secret_encrypted     TEXT,
    active                   BOOLEAN DEFAULT 1,
    invited_by               INTEGER REFERENCES users(id),
    invited_at               TEXT,
    accepted_at              TEXT,
    last_active_at           TEXT,
    suspended_at             TEXT, suspended_reason TEXT
);


CREATE TABLE IF NOT EXISTS admin_audit_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id       INTEGER NOT NULL REFERENCES users(id),
    actor_name     TEXT,                              -- denormalized for retention
    action         TEXT NOT NULL,
    target_type    TEXT,
    target_id      TEXT,
    detail_json    TEXT,
    ip_address     TEXT,
    user_agent     TEXT,
    occurred_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_actor  ON admin_audit_log(actor_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action ON admin_audit_log(action, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_target ON admin_audit_log(target_type, target_id);


CREATE TABLE IF NOT EXISTS api_tokens (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    token_prefix    TEXT NOT NULL,                -- 'sk_vos_abc...' first 10 chars
    token_hash      TEXT NOT NULL,                -- argon2 of full token
    name            TEXT NOT NULL,
    scope           TEXT NOT NULL,                -- admin|readonly|ci
    created_by      INTEGER REFERENCES users(id),
    last_used_at    TEXT,
    last_used_ip    TEXT,
    expires_at      TEXT,
    revoked_at      TEXT, revoked_by INTEGER REFERENCES users(id),
    active          BOOLEAN DEFAULT 1,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_api_tokens_active ON api_tokens(active, revoked_at);


CREATE TABLE IF NOT EXISTS integrations (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    service_name            TEXT UNIQUE NOT NULL,        -- 'shopify' | 'anthropic' | ...
    category                TEXT NOT NULL,               -- ai|commerce|data|email
    purpose                 TEXT,
    config_env_prefix       TEXT,                        -- 'SHOPIFY_'
    status                  TEXT DEFAULT 'not_configured',
    last_health_check       TEXT,
    last_health_status      TEXT,
    last_error              TEXT,
    notes                   TEXT,
    enabled                 BOOLEAN DEFAULT 1
);


CREATE TABLE IF NOT EXISTS integration_metrics (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    integration_id      INTEGER REFERENCES integrations(id),
    bucket_start        TEXT NOT NULL,                 -- hourly
    request_count       INTEGER DEFAULT 0,
    error_count         INTEGER DEFAULT 0,
    latency_p50_ms      INTEGER,
    latency_p95_ms      INTEGER,
    cost_cents          INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_int_metrics ON integration_metrics(integration_id, bucket_start DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_int_metrics_unique_bucket ON integration_metrics(integration_id, bucket_start);


-- =========================================================================
-- Seed data (idempotent)
-- =========================================================================

-- Default trust rules
INSERT OR IGNORE INTO trust_rules (event_kind, delta, description, is_positive) VALUES
    ('submission_approved',          5,   'AI-approved or admin-approved submission', 1),
    ('verification_confirmed',       10,  'Email/handle verified', 1),
    ('shopify_order_attributed',     8,   'Real commerce outcome', 1),
    ('first_sale_via_student_qr',    15,  'Student first activation', 1),
    ('duplicate_submission',         -5,  'Same video submitted multiple times', 0),
    ('suspected_fake_engagement',    -15, 'Views/likes pattern inconsistent', 0),
    ('bot_pattern_detected',         -30, 'Behavior matches known bot signals', 0),
    ('third_violation_blocked',      -50, 'Auto-block after 3 violations', 0);

-- Default genre benchmarks
INSERT OR IGNORE INTO genre_benchmarks (genre, avg_score_target, pass_rate_target, characteristics_json, sample_count) VALUES
    ('unboxing',  6.2, 0.82, '{"length_sec":[45,90],"brand_closeups_min":3,"exposure":"strong","story":"optional"}', 0),
    ('review',    7.8, 0.94, '{"length_min":[5,12],"sample_footage":true,"comparisons_bonus":2,"technical":"high"}', 0),
    ('cinematic', 8.4, 0.48, '{"narrative":true,"color_grade_weight":"high","lighting_weight":"high","brand":"subtle_ok"}', 0);

-- Default integrations registry (env prefixes should match your .env names)
INSERT OR IGNORE INTO integrations (service_name, category, purpose, config_env_prefix, status) VALUES
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
    ('sentry',      'data',     'Error tracking',           'SENTRY_',      'not_configured');
