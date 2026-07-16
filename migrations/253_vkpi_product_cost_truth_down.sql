DROP INDEX IF EXISTS idx_vkpi_product_cost_catalog_truth;

ALTER TABLE vkpi_product_cost_catalog
  DROP CONSTRAINT IF EXISTS chk_vkpi_product_cost_verified_provenance;
ALTER TABLE vkpi_product_cost_catalog
  DROP CONSTRAINT IF EXISTS chk_vkpi_product_cost_verification_status;

ALTER TABLE vkpi_product_cost_catalog DROP COLUMN IF EXISTS verified_at;
ALTER TABLE vkpi_product_cost_catalog DROP COLUMN IF EXISTS verified_by_staff_id;
ALTER TABLE vkpi_product_cost_catalog DROP COLUMN IF EXISTS source_observed_at;
ALTER TABLE vkpi_product_cost_catalog DROP COLUMN IF EXISTS source_ref;
ALTER TABLE vkpi_product_cost_catalog DROP COLUMN IF EXISTS source_type;
ALTER TABLE vkpi_product_cost_catalog DROP COLUMN IF EXISTS verification_status;
ALTER TABLE vkpi_product_cost_catalog DROP COLUMN IF EXISTS row_version;
