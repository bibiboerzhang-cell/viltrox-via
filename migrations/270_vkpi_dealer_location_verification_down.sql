DROP INDEX IF EXISTS idx_vkpi_dealers_google_place_review_queue;
DROP INDEX IF EXISTS idx_vkpi_dealers_physical_map_eligibility;

ALTER TABLE vkpi_dealers
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_published_receipt;

-- Restore migration-260 publication semantics.  Rows fail-closed to draft on
-- rollback; this down migration never guesses which prior rows were stores.
ALTER TABLE vkpi_dealers
  ADD CONSTRAINT chk_vkpi_dealer_published_receipt CHECK (
    publication_status = 'draft'
    OR (
      published_at IS NOT NULL
      AND (
        published_by ~ '^staff_[1-9][0-9]{0,18}$'
        OR published_by = 'system:migration260_legacy_visibility'
      )
      AND lat IS NOT NULL
      AND lng IS NOT NULL
    )
  );

ALTER TABLE vkpi_dealers
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_location_contract_shape,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_google_place_evidence_object,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_google_place_receipt,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_google_place_status,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_physical_store_receipt,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_physical_store_status,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_canonical_location_receipt,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_canonical_location_status,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_location_contract_version;

ALTER TABLE vkpi_dealers
  DROP COLUMN IF EXISTS google_place_evidence_json,
  DROP COLUMN IF EXISTS google_place_checked_by,
  DROP COLUMN IF EXISTS google_place_checked_at,
  DROP COLUMN IF EXISTS google_maps_url,
  DROP COLUMN IF EXISTS google_place_id,
  DROP COLUMN IF EXISTS google_place_verification_status,
  DROP COLUMN IF EXISTS physical_store_verification_note,
  DROP COLUMN IF EXISTS physical_store_checked_by,
  DROP COLUMN IF EXISTS physical_store_checked_at,
  DROP COLUMN IF EXISTS physical_store_status,
  DROP COLUMN IF EXISTS canonical_location_checked_by,
  DROP COLUMN IF EXISTS canonical_location_checked_at,
  DROP COLUMN IF EXISTS canonical_location_status,
  DROP COLUMN IF EXISTS location_verification_contract_version;

DELETE FROM schema_migrations
WHERE version_key = '270_vkpi_dealer_location_verification.sql';
