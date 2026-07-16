-- 263: Source-backed inventory quantity verification receipts.
--
-- Existing inventory rows remain unverified.  This migration does not import,
-- infer, or confirm any quantity.  A later owner/admin action must bind the
-- already-stored quantity to a source receipt through a CAS-protected route.

ALTER TABLE vkpi_inventory
  ADD COLUMN IF NOT EXISTS quantity_source_ref TEXT,
  ADD COLUMN IF NOT EXISTS quantity_source_observed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS quantity_evidence_sha256 CHAR(64),
  ADD COLUMN IF NOT EXISTS quantity_verified_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS quantity_verified_organization_id BIGINT REFERENCES organizations(id) ON DELETE RESTRICT,
  ADD COLUMN IF NOT EXISTS row_version BIGINT NOT NULL DEFAULT 1;

-- Migration 240 allowed status labels before durable provenance existed.  Do
-- not grandfather such labels into verified stock; fail closed without
-- changing the stored quantity itself.
UPDATE vkpi_inventory
SET quantity_status='unverified',
    quantity_source='legacy_verification_without_receipt',
    quantity_verified_at=NULL,
    quantity_source_ref=NULL,
    quantity_source_observed_at=NULL,
    quantity_evidence_sha256=NULL,
    quantity_verified_by_staff_id=NULL,
    quantity_verified_organization_id=NULL,
    row_version=row_version + 1,
    updated_at=NOW()
WHERE quantity_status IN ('manual_confirmed', 'source_confirmed');

ALTER TABLE vkpi_inventory
  DROP CONSTRAINT IF EXISTS chk_vkpi_inventory_quantity_receipt,
  DROP CONSTRAINT IF EXISTS chk_vkpi_inventory_quantity_evidence_sha256,
  DROP CONSTRAINT IF EXISTS chk_vkpi_inventory_row_version;

ALTER TABLE vkpi_inventory
  ADD CONSTRAINT chk_vkpi_inventory_quantity_receipt CHECK (
    quantity_status = 'unverified'
    OR (
      NULLIF(TRIM(COALESCE(quantity_source, '')), '') IS NOT NULL
      AND quantity_source NOT IN (
        'unknown', 'catalog_reference', 'legacy_demo_seed', 'legacy_unverified',
        'manual_reference', 'manual_placeholder', 'manual_adjustment_reference'
      )
      AND NULLIF(TRIM(COALESCE(quantity_source_ref, '')), '') IS NOT NULL
      AND quantity_source_observed_at IS NOT NULL
      AND quantity_evidence_sha256 IS NOT NULL
      AND quantity_verified_by_staff_id IS NOT NULL
      AND quantity_verified_organization_id IS NOT NULL
      AND quantity_verified_at IS NOT NULL
      AND quantity_source_observed_at <= quantity_verified_at + INTERVAL '5 minutes'
    )
  ),
  ADD CONSTRAINT chk_vkpi_inventory_quantity_evidence_sha256 CHECK (
    quantity_evidence_sha256 IS NULL
    OR quantity_evidence_sha256 ~ '^[0-9a-f]{64}$'
  ),
  ADD CONSTRAINT chk_vkpi_inventory_row_version CHECK (row_version >= 1);

CREATE INDEX IF NOT EXISTS idx_vkpi_inventory_quantity_verified_receipt
  ON vkpi_inventory(quantity_verified_organization_id, quantity_status, quantity_verified_at DESC)
  WHERE quantity_status IN ('manual_confirmed', 'source_confirmed');

COMMENT ON COLUMN vkpi_inventory.quantity_source_ref IS
  'Non-secret source receipt reference. It does not contain provider credentials.';
COMMENT ON COLUMN vkpi_inventory.quantity_evidence_sha256 IS
  'SHA-256 of the immutable source artifact used to verify the already-stored quantity.';
COMMENT ON COLUMN vkpi_inventory.row_version IS
  'Optimistic concurrency version. Quantity verification and invalidation require an exact match.';
