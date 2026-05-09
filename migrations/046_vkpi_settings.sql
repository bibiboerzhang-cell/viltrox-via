-- V-KPI feature flags, crawl settings, budget controls, training exports, and behavior ledger.
CREATE TABLE IF NOT EXISTS vkpi_feature_flags (
    flag_key TEXT PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    description TEXT DEFAULT '',
    updated_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS vkpi_platform_crawl_settings (
    platform TEXT PRIMARY KEY,
    crawl_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    daily_account_limit INTEGER NOT NULL DEFAULT 0,
    posts_per_account INTEGER NOT NULL DEFAULT 0,
    crawl_comments BOOLEAN NOT NULL DEFAULT FALSE,
    crawl_followers BOOLEAN NOT NULL DEFAULT FALSE,
    crawl_audience_graph BOOLEAN NOT NULL DEFAULT FALSE,
    only_uncontacted_kols BOOLEAN NOT NULL DEFAULT TRUE,
    include_company_accounts BOOLEAN NOT NULL DEFAULT TRUE,
    include_competitor_accounts BOOLEAN NOT NULL DEFAULT TRUE,
    include_candidate_kols BOOLEAN NOT NULL DEFAULT TRUE,
    monthly_budget_usd NUMERIC(10,2) NOT NULL DEFAULT 0,
    failure_threshold INTEGER NOT NULL DEFAULT 5,
    last_test_status TEXT NOT NULL DEFAULT 'not_configured',
    last_test_at TIMESTAMPTZ,
    updated_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS vkpi_budget_settings (
    budget_key TEXT PRIMARY KEY,
    monthly_limit_usd NUMERIC(10,2) NOT NULL DEFAULT 0,
    current_month_spent NUMERIC(10,2) NOT NULL DEFAULT 0,
    alert_threshold_pct INTEGER NOT NULL DEFAULT 80,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    updated_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS vkpi_training_exports (
    id BIGSERIAL PRIMARY KEY,
    export_uid TEXT NOT NULL UNIQUE,
    date_from TEXT DEFAULT '',
    date_to TEXT DEFAULT '',
    file_path TEXT DEFAULT '',
    row_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'queued',
    created_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS vkpi_behavior_ledger (
    id BIGSERIAL PRIMARY KEY,
    actor_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    context_json TEXT NOT NULL DEFAULT '{}',
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_behavior_ledger_entity ON vkpi_behavior_ledger(entity_type, entity_id, occurred_at DESC);
