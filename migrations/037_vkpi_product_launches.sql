-- V-KPI Product Analysis launch briefs.
CREATE TABLE IF NOT EXISTS vkpi_product_launches (
    id BIGSERIAL PRIMARY KEY,
    launch_uid TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    product_sku TEXT DEFAULT '',
    product_name TEXT DEFAULT '',
    category TEXT DEFAULT '',
    target_market TEXT DEFAULT '',
    target_platforms_json TEXT NOT NULL DEFAULT '[]',
    target_audience_json TEXT NOT NULL DEFAULT '{}',
    competitor_products_json TEXT NOT NULL DEFAULT '[]',
    launch_window_start TIMESTAMPTZ,
    launch_window_end TIMESTAMPTZ,
    budget_range_json TEXT NOT NULL DEFAULT '{}',
    goals_json TEXT NOT NULL DEFAULT '{}',
    constraints_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'draft',
    created_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_vkpi_product_launches_status
    ON vkpi_product_launches(status, updated_at DESC);
