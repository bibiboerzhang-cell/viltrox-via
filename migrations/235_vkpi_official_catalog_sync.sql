-- 235: viltrox.com public Shopify catalog daily sync.
-- Product writes occur only after a complete /products.json?limit=250 feed validates.
-- Missing rows are retained and become store_unlisted only after two complete feeds.
-- The migration runner owns the transaction and fleet advisory lock.  Do not
-- add BEGIN/COMMIT here.
ALTER TABLE vkpi_products
    ADD COLUMN IF NOT EXISTS official_catalog_product_id TEXT NOT NULL DEFAULT '';
ALTER TABLE vkpi_products
    ADD COLUMN IF NOT EXISTS official_catalog_variant_id TEXT NOT NULL DEFAULT '';
ALTER TABLE vkpi_products
    ADD COLUMN IF NOT EXISTS official_catalog_last_seen_at TIMESTAMPTZ;
ALTER TABLE vkpi_products
    ADD COLUMN IF NOT EXISTS official_catalog_missing_full_feeds INT NOT NULL DEFAULT 0
        CHECK (official_catalog_missing_full_feeds >= 0);
ALTER TABLE vkpi_products
    ADD COLUMN IF NOT EXISTS official_catalog_previous_status TEXT NOT NULL DEFAULT '';

CREATE UNIQUE INDEX IF NOT EXISTS idx_vkpi_products_official_product
    ON vkpi_products(official_catalog_product_id)
    WHERE official_catalog_product_id <> '';
CREATE INDEX IF NOT EXISTS idx_vkpi_products_official_variant
    ON vkpi_products(official_catalog_variant_id)
    WHERE official_catalog_variant_id <> '';
CREATE INDEX IF NOT EXISTS idx_vkpi_products_official_missing
    ON vkpi_products(official_catalog_missing_full_feeds, status)
    WHERE official_catalog_variant_id <> '';

CREATE TABLE IF NOT EXISTS vkpi_official_catalog_sync_runs (
    run_id TEXT PRIMARY KEY,
    source_url TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    products_fetched INT NOT NULL DEFAULT 0,
    variants_fetched INT NOT NULL DEFAULT 0,
    inserted_count INT NOT NULL DEFAULT 0,
    updated_count INT NOT NULL DEFAULT 0,
    unchanged_count INT NOT NULL DEFAULT 0,
    missing_count INT NOT NULL DEFAULT 0,
    store_unlisted_count INT NOT NULL DEFAULT 0,
    duration_ms INT NOT NULL DEFAULT 0 CHECK (duration_ms >= 0),
    error_type TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT ''
);

ALTER TABLE vkpi_official_catalog_sync_runs
    ADD COLUMN IF NOT EXISTS unchanged_count INT NOT NULL DEFAULT 0;
ALTER TABLE vkpi_official_catalog_sync_runs
    ADD COLUMN IF NOT EXISTS duration_ms INT NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_vkpi_official_catalog_runs_started
    ON vkpi_official_catalog_sync_runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_official_catalog_runs_status
    ON vkpi_official_catalog_sync_runs(status, started_at DESC);

CREATE TABLE IF NOT EXISTS vkpi_official_catalog_sync_items (
    run_id TEXT NOT NULL REFERENCES vkpi_official_catalog_sync_runs(run_id) ON DELETE CASCADE,
    sku TEXT NOT NULL,
    generated_sku TEXT NOT NULL,
    shopify_product_id TEXT NOT NULL,
    shopify_variant_id TEXT NOT NULL,
    PRIMARY KEY (run_id, sku)
);

ALTER TABLE vkpi_official_catalog_sync_items
    ADD COLUMN IF NOT EXISTS generated_sku TEXT NOT NULL DEFAULT '';
CREATE UNIQUE INDEX IF NOT EXISTS idx_vkpi_official_catalog_item_product
    ON vkpi_official_catalog_sync_items(run_id, shopify_product_id);

INSERT INTO scheduler_tasks
    (task_key, label, enabled, max_daily_runs, max_daily_cost_cents,
     allowed_hours, owner, risk_level)
VALUES
    ('vkpi_official_catalog_sync', 'viltrox.com 官网公开产品目录每日同步', TRUE, 1, 0,
     '03:00-04:00 Asia/Shanghai', 'marketing_ops', 'low')
ON CONFLICT (task_key) DO NOTHING;
