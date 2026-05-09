-- V-OS Activities Growth OS Phase A.
-- Postgres runtime migration. SQLite local runtime is handled by
-- backend/app/db/migrations_activities.py to keep both runtimes compatible.

CREATE TABLE IF NOT EXISTS activities (
    id BIGSERIAL PRIMARY KEY,
    activity_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    title_en TEXT DEFAULT '',
    description TEXT DEFAULT '',
    activity_type TEXT NOT NULL DEFAULT 'event',
    market TEXT NOT NULL DEFAULT 'GLOBAL',
    country TEXT DEFAULT '',
    platform TEXT DEFAULT 'offline',
    product_sku TEXT DEFAULT '',
    dealer_name TEXT DEFAULT '',
    dealer_id BIGINT,
    location_name TEXT DEFAULT '',
    location_address TEXT DEFAULT '',
    location_lat DOUBLE PRECISION,
    location_lng DOUBLE PRECISION,
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    planned_budget_cents BIGINT NOT NULL DEFAULT 0,
    actual_spend_cents BIGINT NOT NULL DEFAULT 0,
    budget_breakdown_json TEXT NOT NULL DEFAULT '{}',
    qr_token TEXT UNIQUE NOT NULL,
    qr_image_url TEXT DEFAULT '',
    landing_url TEXT DEFAULT '',
    utm_source TEXT DEFAULT 'activity',
    utm_campaign TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    submit_attribution_hours INTEGER DEFAULT 168,
    purchase_attribution_hours INTEGER DEFAULT 720,
    eligible_school_id BIGINT,
    require_edu_email INTEGER DEFAULT 0,
    created_by_staff_id BIGINT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_activities_status_starts ON activities(status, starts_at DESC);
CREATE INDEX IF NOT EXISTS idx_activities_qr_token ON activities(qr_token);
CREATE INDEX IF NOT EXISTS idx_activities_market_type ON activities(market, activity_type);
CREATE INDEX IF NOT EXISTS idx_activities_country_platform ON activities(country, platform);
CREATE INDEX IF NOT EXISTS idx_activities_product_sku ON activities(product_sku);

CREATE TABLE IF NOT EXISTS activity_attributions (
    id BIGSERIAL PRIMARY KEY,
    activity_id BIGINT NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    user_id BIGINT,
    party_id TEXT,
    anonymous_id TEXT,
    event_token TEXT,
    first_scan_at TEXT NOT NULL,
    last_touch_at TEXT,
    first_register_at TEXT,
    edu_verified_at TEXT,
    first_submit_at TEXT,
    first_purchase_at TEXT,
    submission_count INTEGER NOT NULL DEFAULT 0,
    order_count INTEGER NOT NULL DEFAULT 0,
    revenue_cents BIGINT NOT NULL DEFAULT 0,
    commission_cents BIGINT NOT NULL DEFAULT 0,
    attribution_type TEXT NOT NULL DEFAULT 'first_touch',
    manual_attributed_by BIGINT,
    manual_note TEXT DEFAULT '',
    user_agent TEXT DEFAULT '',
    ip_hash TEXT DEFAULT '',
    device_fingerprint TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_act_attr_activity_user_unique
    ON activity_attributions(activity_id, user_id)
    WHERE user_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_act_attr_activity_anon_unique
    ON activity_attributions(activity_id, anonymous_id)
    WHERE anonymous_id IS NOT NULL AND anonymous_id != '';
CREATE INDEX IF NOT EXISTS idx_act_attr_activity ON activity_attributions(activity_id, first_scan_at DESC);
CREATE INDEX IF NOT EXISTS idx_act_attr_user ON activity_attributions(user_id, last_touch_at DESC);
CREATE INDEX IF NOT EXISTS idx_act_attr_token ON activity_attributions(event_token);

CREATE TABLE IF NOT EXISTS activity_events (
    id BIGSERIAL PRIMARY KEY,
    activity_id BIGINT NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    user_id BIGINT,
    party_id TEXT,
    anonymous_id TEXT,
    event_token TEXT,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    revenue_cents BIGINT DEFAULT 0,
    commission_cents BIGINT DEFAULT 0,
    user_agent TEXT DEFAULT '',
    ip_hash TEXT DEFAULT '',
    referrer TEXT DEFAULT '',
    source_path TEXT DEFAULT '',
    idempotency_key TEXT,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_act_events_idempotency
    ON activity_events(idempotency_key)
    WHERE idempotency_key IS NOT NULL AND idempotency_key != '';
CREATE INDEX IF NOT EXISTS idx_act_events_activity_time ON activity_events(activity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_act_events_type ON activity_events(activity_id, event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_act_events_user ON activity_events(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_act_events_token ON activity_events(event_token);
