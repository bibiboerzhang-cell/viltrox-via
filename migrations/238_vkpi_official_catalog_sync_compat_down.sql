BEGIN;

DROP INDEX IF EXISTS idx_vkpi_official_catalog_item_product;

ALTER TABLE vkpi_official_catalog_sync_items
    DROP COLUMN IF EXISTS generated_sku;
ALTER TABLE vkpi_official_catalog_sync_runs
    DROP COLUMN IF EXISTS duration_ms;
ALTER TABLE vkpi_official_catalog_sync_runs
    DROP COLUMN IF EXISTS unchanged_count;

COMMIT;
