-- 235 down: remove official catalog sync audit/state and scheduler registration.
BEGIN;

DELETE FROM scheduler_tasks WHERE task_key = 'vkpi_official_catalog_sync';

DROP TABLE IF EXISTS vkpi_official_catalog_sync_items;
DROP TABLE IF EXISTS vkpi_official_catalog_sync_runs;

DROP INDEX IF EXISTS idx_vkpi_products_official_missing;
DROP INDEX IF EXISTS idx_vkpi_products_official_variant;
DROP INDEX IF EXISTS idx_vkpi_products_official_product;

ALTER TABLE vkpi_products DROP COLUMN IF EXISTS official_catalog_previous_status;
ALTER TABLE vkpi_products DROP COLUMN IF EXISTS official_catalog_missing_full_feeds;
ALTER TABLE vkpi_products DROP COLUMN IF EXISTS official_catalog_last_seen_at;
ALTER TABLE vkpi_products DROP COLUMN IF EXISTS official_catalog_variant_id;
ALTER TABLE vkpi_products DROP COLUMN IF EXISTS official_catalog_product_id;

COMMIT;
