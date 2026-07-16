-- Downgrade is fail-closed: remove verification claims before dropping their
-- receipts.  Quantities remain stored as unverified reference values.
UPDATE vkpi_inventory
SET quantity_status='unverified',
    quantity_source='verification_schema_rolled_back',
    quantity_verified_at=NULL,
    updated_at=NOW()
WHERE quantity_status IN ('manual_confirmed', 'source_confirmed');

DROP INDEX IF EXISTS idx_vkpi_inventory_quantity_verified_receipt;

ALTER TABLE vkpi_inventory
  DROP CONSTRAINT IF EXISTS chk_vkpi_inventory_quantity_receipt,
  DROP CONSTRAINT IF EXISTS chk_vkpi_inventory_quantity_evidence_sha256,
  DROP CONSTRAINT IF EXISTS chk_vkpi_inventory_row_version;

ALTER TABLE vkpi_inventory
  DROP COLUMN IF EXISTS row_version,
  DROP COLUMN IF EXISTS quantity_verified_organization_id,
  DROP COLUMN IF EXISTS quantity_verified_by_staff_id,
  DROP COLUMN IF EXISTS quantity_evidence_sha256,
  DROP COLUMN IF EXISTS quantity_source_observed_at,
  DROP COLUMN IF EXISTS quantity_source_ref;

DELETE FROM schema_migrations
WHERE version_key = '263_vkpi_inventory_quantity_provenance.sql';
