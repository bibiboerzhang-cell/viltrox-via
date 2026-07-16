-- 259: Durable reviewed-Dealer identity and evidence receipt.
--
-- This migration creates no Dealer rows and does not backfill legacy rows.
-- Existing rows remain review_contract_version=0 and therefore fail the new
-- durable-review gate until a human-reviewed receipt is written explicitly.
-- A public retailer page is still not Viltrox authorization, current inventory,
-- sales, ROI, or local impact.
--
-- The migration runner owns the transaction and fleet advisory lock.

ALTER TABLE vkpi_dealers
  ADD COLUMN IF NOT EXISTS source_id TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS stable_org_key TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS stable_location_key TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS reviewer_id TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS review_contract_version SMALLINT NOT NULL DEFAULT 0;

ALTER TABLE vkpi_dealers
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_review_source_id;
ALTER TABLE vkpi_dealers
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_review_stable_org;
ALTER TABLE vkpi_dealers
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_review_stable_location;
ALTER TABLE vkpi_dealers
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_reviewer_id;
ALTER TABLE vkpi_dealers
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_evidence_object;
ALTER TABLE vkpi_dealers
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_review_contract_version;
ALTER TABLE vkpi_dealers
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_review_contract_shape;

ALTER TABLE vkpi_dealers
  ADD CONSTRAINT chk_vkpi_dealer_review_source_id CHECK (
    source_id = '' OR source_id ~ '^[a-z0-9][a-z0-9_-]{5,127}$'
  ),
  ADD CONSTRAINT chk_vkpi_dealer_review_stable_org CHECK (
    stable_org_key = '' OR stable_org_key ~ '^dealer_org_[a-z0-9]{8,64}$'
  ),
  ADD CONSTRAINT chk_vkpi_dealer_review_stable_location CHECK (
    stable_location_key = '' OR stable_location_key ~ '^dealer_loc_[a-z0-9]{8,64}$'
  ),
  ADD CONSTRAINT chk_vkpi_dealer_reviewer_id CHECK (
    reviewer_id = '' OR reviewer_id ~ '^staff_[1-9][0-9]{0,18}$'
  ),
  ADD CONSTRAINT chk_vkpi_dealer_evidence_object CHECK (
    jsonb_typeof(evidence_json) = 'object'
  ),
  ADD CONSTRAINT chk_vkpi_dealer_review_contract_version CHECK (
    review_contract_version IN (0, 1)
  ),
  ADD CONSTRAINT chk_vkpi_dealer_review_contract_shape CHECK (
    review_contract_version = 0
    OR (
      source_status = 'public_listing_verified'
      AND source_id <> ''
      AND stable_org_key <> ''
      AND stable_location_key <> ''
      AND reviewer_id <> ''
      AND reviewed_at IS NOT NULL
      AND source_checked_at IS NOT NULL
      AND NULLIF(TRIM(location_source_url), '') IS NOT NULL
      AND NULLIF(TRIM(brand_listing_url), '') IS NOT NULL
      AND evidence_json <> '{}'::jsonb
      AND evidence_json ->> 'claim_status' = 'descriptive_only'
      AND jsonb_typeof(evidence_json -> 'source') = 'object'
      AND jsonb_typeof(evidence_json -> 'product') = 'object'
      AND evidence_json #>> '{source,source_id}' = source_id
      AND evidence_json #>> '{source,source_url}' = location_source_url
      AND evidence_json #>> '{source,reviewer_id}' = reviewer_id
      AND evidence_json #>> '{source,value_status}' = 'observed'
      AND evidence_json #>> '{product,source_url}' = brand_listing_url
      AND evidence_json #>> '{product,value_status}' = 'observed'
    )
  );

CREATE INDEX IF NOT EXISTS idx_vkpi_dealer_review_source_id
  ON vkpi_dealers(source_id)
  WHERE review_contract_version = 1;

CREATE UNIQUE INDEX IF NOT EXISTS uq_vkpi_dealer_review_stable_location
  ON vkpi_dealers(stable_location_key)
  WHERE review_contract_version = 1;

CREATE INDEX IF NOT EXISTS idx_vkpi_dealer_review_stable_org
  ON vkpi_dealers(stable_org_key, state)
  WHERE review_contract_version = 1;

CREATE INDEX IF NOT EXISTS idx_vkpi_dealer_review_queue
  ON vkpi_dealers(review_contract_version, source_status, source_checked_at DESC);

COMMENT ON COLUMN vkpi_dealers.source_id IS
  'Human-reviewed public source identity for one exact Dealer location; never a Viltrox authorization claim.';
COMMENT ON COLUMN vkpi_dealers.stable_location_key IS
  'Exact human-reviewed location identity. Empty or review_contract_version=0 cannot qualify for the Dealer map.';
COMMENT ON COLUMN vkpi_dealers.evidence_json IS
  'Bounded descriptive evidence receipt for the reviewed source and public product page. Not inventory, sales, ROI, or local-impact evidence.';
COMMENT ON COLUMN vkpi_dealers.review_contract_version IS
  '0=legacy/unverified receipt, 1=durable reviewed identity/evidence contract. No automatic promotion is allowed.';
