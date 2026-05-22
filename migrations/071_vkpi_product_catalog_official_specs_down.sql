DROP INDEX IF EXISTS idx_products_source_confidence;
DROP INDEX IF EXISTS idx_products_series_mount;

ALTER TABLE vkpi_products DROP COLUMN IF EXISTS source_confidence;
ALTER TABLE vkpi_products DROP COLUMN IF EXISTS source_checked_at;
ALTER TABLE vkpi_products DROP COLUMN IF EXISTS source_url;
ALTER TABLE vkpi_products DROP COLUMN IF EXISTS fit_tags_json;
ALTER TABLE vkpi_products DROP COLUMN IF EXISTS specs_json;
ALTER TABLE vkpi_products DROP COLUMN IF EXISTS product_url;
ALTER TABLE vkpi_products DROP COLUMN IF EXISTS mount;
ALTER TABLE vkpi_products DROP COLUMN IF EXISTS series;
