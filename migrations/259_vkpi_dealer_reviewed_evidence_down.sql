-- Roll back migration 259 only while no v1 reviewed receipt exists.

DO $rollback_guard$
BEGIN
  IF EXISTS (
    SELECT 1 FROM vkpi_dealers
    WHERE review_contract_version <> 0
       OR source_id <> ''
       OR stable_org_key <> ''
       OR stable_location_key <> ''
       OR reviewer_id <> ''
       OR reviewed_at IS NOT NULL
       OR evidence_json <> '{}'::jsonb
  ) THEN
    RAISE EXCEPTION 'cannot roll back 259 while reviewed Dealer evidence receipts exist';
  END IF;
END
$rollback_guard$;

DROP INDEX IF EXISTS idx_vkpi_dealer_review_queue;
DROP INDEX IF EXISTS idx_vkpi_dealer_review_stable_org;
DROP INDEX IF EXISTS uq_vkpi_dealer_review_stable_location;
DROP INDEX IF EXISTS idx_vkpi_dealer_review_source_id;

ALTER TABLE vkpi_dealers
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_review_contract_shape,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_review_contract_version,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_evidence_object,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_reviewer_id,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_review_stable_location,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_review_stable_org,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_review_source_id,
  DROP COLUMN IF EXISTS review_contract_version,
  DROP COLUMN IF EXISTS evidence_json,
  DROP COLUMN IF EXISTS reviewed_at,
  DROP COLUMN IF EXISTS reviewer_id,
  DROP COLUMN IF EXISTS stable_location_key,
  DROP COLUMN IF EXISTS stable_org_key,
  DROP COLUMN IF EXISTS source_id;

DELETE FROM schema_migrations
WHERE version_key = '259_vkpi_dealer_reviewed_evidence.sql';
