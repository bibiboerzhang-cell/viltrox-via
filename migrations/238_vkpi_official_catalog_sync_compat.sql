-- 238: Compatibility repair for databases that already recorded migration 235.
-- Migration 235 originally shipped without these observability/identity columns;
-- never edit an applied migration and expect existing databases to replay it.
-- The migration runner owns the transaction and fleet advisory lock.  Do not
-- add BEGIN/COMMIT here.
ALTER TABLE vkpi_official_catalog_sync_runs
    ADD COLUMN IF NOT EXISTS unchanged_count INT NOT NULL DEFAULT 0;
ALTER TABLE vkpi_official_catalog_sync_runs
    ADD COLUMN IF NOT EXISTS duration_ms INT NOT NULL DEFAULT 0;

ALTER TABLE vkpi_official_catalog_sync_items
    ADD COLUMN IF NOT EXISTS generated_sku TEXT NOT NULL DEFAULT '';

CREATE UNIQUE INDEX IF NOT EXISTS idx_vkpi_official_catalog_item_product
    ON vkpi_official_catalog_sync_items(run_id, shopify_product_id);
