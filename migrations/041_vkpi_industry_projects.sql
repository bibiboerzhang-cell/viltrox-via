-- V-KPI Industry Data projects.
CREATE TABLE IF NOT EXISTS vkpi_industry_projects (
    id BIGSERIAL PRIMARY KEY,
    project_uid TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    project_type TEXT NOT NULL DEFAULT 'brand_monitor',
    linked_launch_id BIGINT REFERENCES vkpi_product_launches(id) ON DELETE SET NULL,
    linked_campaign_id BIGINT,
    monitoring_frequency TEXT NOT NULL DEFAULT 'daily',
    auto_archive_days INTEGER,
    report_subscriptions_json TEXT NOT NULL DEFAULT '{}',
    owner_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    archived_at TIMESTAMPTZ,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    idempotency_key TEXT UNIQUE
);
