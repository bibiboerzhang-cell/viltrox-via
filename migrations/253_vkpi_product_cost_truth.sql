-- 253: Product cost truth contract.
-- Existing catalog rows are historical/reference material until a manager
-- explicitly verifies a source.  Runner owns the transaction boundary.

ALTER TABLE vkpi_product_cost_catalog
  ADD COLUMN IF NOT EXISTS verification_status TEXT NOT NULL DEFAULT 'reference_unverified';

ALTER TABLE vkpi_product_cost_catalog
  ADD COLUMN IF NOT EXISTS row_version BIGINT NOT NULL DEFAULT 1;

ALTER TABLE vkpi_product_cost_catalog
  ADD COLUMN IF NOT EXISTS source_type TEXT NOT NULL DEFAULT '';

ALTER TABLE vkpi_product_cost_catalog
  ADD COLUMN IF NOT EXISTS source_ref TEXT NOT NULL DEFAULT '';

ALTER TABLE vkpi_product_cost_catalog
  ADD COLUMN IF NOT EXISTS source_observed_at TIMESTAMPTZ;

ALTER TABLE vkpi_product_cost_catalog
  ADD COLUMN IF NOT EXISTS verified_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL;

ALTER TABLE vkpi_product_cost_catalog
  ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ;

UPDATE vkpi_product_cost_catalog
SET verification_status='reference_unverified',
    verified_by_staff_id=NULL,
    verified_at=NULL
WHERE verification_status IS NULL
   OR verification_status NOT IN ('reference_unverified','verified','rejected');

-- Fail closed for any legacy/direct SQL row that only claimed `verified`
-- without the provenance bundle required by the application boundary.
UPDATE vkpi_product_cost_catalog
SET verification_status='reference_unverified',
    verified_by_staff_id=NULL,
    verified_at=NULL
WHERE verification_status='verified'
  AND (
    NULLIF(BTRIM(source_type), '') IS NULL
    OR NULLIF(BTRIM(source_ref), '') IS NULL
    OR source_observed_at IS NULL
    OR verified_by_staff_id IS NULL
    OR verified_at IS NULL
  );

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname='chk_vkpi_product_cost_verification_status'
  ) THEN
    ALTER TABLE vkpi_product_cost_catalog
      ADD CONSTRAINT chk_vkpi_product_cost_verification_status
      CHECK (verification_status IN ('reference_unverified','verified','rejected'));
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname='chk_vkpi_product_cost_verified_provenance'
  ) THEN
    ALTER TABLE vkpi_product_cost_catalog
      ADD CONSTRAINT chk_vkpi_product_cost_verified_provenance
      CHECK (
        verification_status <> 'verified'
        OR (
          NULLIF(BTRIM(source_type), '') IS NOT NULL
          AND NULLIF(BTRIM(source_ref), '') IS NOT NULL
          AND source_observed_at IS NOT NULL
          AND verified_by_staff_id IS NOT NULL
          AND verified_at IS NOT NULL
        )
      );
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_vkpi_product_cost_catalog_truth
  ON vkpi_product_cost_catalog(verification_status, active, product_sku);

COMMENT ON COLUMN vkpi_product_cost_catalog.verification_status IS
  'Only verified rows may auto-post an actual product cost. Existing/history rows remain reference_unverified.';
