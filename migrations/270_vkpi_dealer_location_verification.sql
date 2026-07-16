-- 270: Dealer physical-location verification contract.
--
-- Canonical identity comes from the retailer/store-owned website and its
-- published address.  Google Place/Maps is only an optional cross-check.
-- Product/brand evidence remains in brand_listing_url and
-- vkpi_dealer_brand_relationships and never proves that a row is a physical
-- store.  No Dealer rows or Google-derived values are created here.

ALTER TABLE vkpi_dealers
  ADD COLUMN IF NOT EXISTS location_verification_contract_version SMALLINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS canonical_location_status TEXT NOT NULL DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS canonical_location_checked_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS canonical_location_checked_by TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS physical_store_status TEXT NOT NULL DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS physical_store_checked_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS physical_store_checked_by TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS physical_store_verification_note TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS google_place_verification_status TEXT NOT NULL DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS google_place_id TEXT,
  ADD COLUMN IF NOT EXISTS google_maps_url TEXT,
  ADD COLUMN IF NOT EXISTS google_place_checked_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS google_place_checked_by TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS google_place_evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE vkpi_dealers
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_location_contract_version,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_canonical_location_status,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_canonical_location_receipt,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_physical_store_status,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_physical_store_receipt,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_google_place_status,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_google_place_receipt,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_google_place_evidence_object,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_location_contract_shape;

ALTER TABLE vkpi_dealers
  ADD CONSTRAINT chk_vkpi_dealer_location_contract_version CHECK (
    location_verification_contract_version IN (0, 1)
  ),
  ADD CONSTRAINT chk_vkpi_dealer_canonical_location_status CHECK (
    canonical_location_status IN (
      'pending', 'official_site_verified', 'conflict', 'rejected'
    )
  ),
  ADD CONSTRAINT chk_vkpi_dealer_canonical_location_receipt CHECK (
    canonical_location_status = 'pending'
    OR (
      canonical_location_checked_at IS NOT NULL
      AND canonical_location_checked_by ~ '^staff_[1-9][0-9]{0,18}$'
      AND (
        canonical_location_status <> 'official_site_verified'
        OR (
          NULLIF(TRIM(address), '') IS NOT NULL
          AND NULLIF(TRIM(city), '') IS NOT NULL
          AND NULLIF(TRIM(state), '') IS NOT NULL
          AND NULLIF(TRIM(location_source_url), '') IS NOT NULL
          AND location_source_url ~* '^https?://[^[:space:]]+$'
          AND location_source_url !~* '^https?://([^/]+\.)?(nikonusa\.com|nikon\.com|canon\.com|sony\.com|godox\.com)(/|$)'
        )
      )
    )
  ),
  ADD CONSTRAINT chk_vkpi_dealer_physical_store_status CHECK (
    physical_store_status IN (
      'pending', 'verified_physical_store', 'not_physical_store', 'conflict'
    )
  ),
  ADD CONSTRAINT chk_vkpi_dealer_physical_store_receipt CHECK (
    physical_store_status = 'pending'
    OR (
      physical_store_checked_at IS NOT NULL
      AND physical_store_checked_by ~ '^staff_[1-9][0-9]{0,18}$'
      AND (
        physical_store_status <> 'verified_physical_store'
        OR canonical_location_status = 'official_site_verified'
      )
    )
  ),
  ADD CONSTRAINT chk_vkpi_dealer_google_place_status CHECK (
    google_place_verification_status IN (
      'pending', 'matched', 'conflict', 'not_found'
    )
  ),
  ADD CONSTRAINT chk_vkpi_dealer_google_place_evidence_object CHECK (
    jsonb_typeof(google_place_evidence_json) = 'object'
  ),
  ADD CONSTRAINT chk_vkpi_dealer_google_place_receipt CHECK (
    google_place_verification_status = 'pending'
    OR (
      google_place_checked_at IS NOT NULL
      AND google_place_checked_by ~ '^staff_[1-9][0-9]{0,18}$'
      AND (
        google_place_verification_status <> 'matched'
        OR (
          canonical_location_status = 'official_site_verified'
          AND NULLIF(TRIM(google_place_id), '') IS NOT NULL
          AND google_place_id ~ '^[A-Za-z0-9_-]{10,255}$'
          AND NULLIF(TRIM(google_maps_url), '') IS NOT NULL
          AND google_maps_url ~* '^https://(www\.)?google\.[^/]+/maps/'
          AND google_place_evidence_json ->> 'provider' IS NOT NULL
          AND google_place_evidence_json ->> 'provider' = 'google_places'
          AND google_place_evidence_json ->> 'value_status' IS NOT NULL
          AND google_place_evidence_json ->> 'value_status' = 'observed'
        )
      )
    )
  ),
  ADD CONSTRAINT chk_vkpi_dealer_location_contract_shape CHECK (
    location_verification_contract_version = 0
    OR (
      canonical_location_status <> 'pending'
      AND physical_store_status <> 'pending'
      AND canonical_location_checked_at IS NOT NULL
      AND physical_store_checked_at IS NOT NULL
      AND NULLIF(TRIM(physical_store_verification_note), '') IS NOT NULL
    )
  );

-- Fail closed: migration 260 allowed a manager to publish coordinates without
-- proving that the record represented a physical storefront.  Preserve every
-- row and every evidence field, but remove old visibility until contract v1 is
-- reviewed.  Manufacturer directory organization names therefore cannot stay
-- on the map merely because coordinates were attached.
UPDATE vkpi_dealers
SET publication_status = 'draft',
    published_at = NULL,
    published_by = '',
    updated_at = NOW()
WHERE publication_status = 'published'
  AND (
    location_verification_contract_version <> 1
    OR canonical_location_status <> 'official_site_verified'
    OR physical_store_status <> 'verified_physical_store'
  );

ALTER TABLE vkpi_dealers
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_published_receipt;

ALTER TABLE vkpi_dealers
  ADD CONSTRAINT chk_vkpi_dealer_published_receipt CHECK (
    publication_status = 'draft'
    OR (
      published_at IS NOT NULL
      AND published_by ~ '^staff_[1-9][0-9]{0,18}$'
      AND lat IS NOT NULL
      AND lng IS NOT NULL
      AND location_verification_contract_version = 1
      AND canonical_location_status = 'official_site_verified'
      AND physical_store_status = 'verified_physical_store'
    )
  );

CREATE INDEX IF NOT EXISTS idx_vkpi_dealers_physical_map_eligibility
  ON vkpi_dealers(publication_status, physical_store_status, state, city)
  WHERE location_verification_contract_version = 1
    AND canonical_location_status = 'official_site_verified'
    AND physical_store_status = 'verified_physical_store';

CREATE INDEX IF NOT EXISTS idx_vkpi_dealers_google_place_review_queue
  ON vkpi_dealers(google_place_verification_status, canonical_location_checked_at DESC);

COMMENT ON COLUMN vkpi_dealers.canonical_location_status IS
  'Canonical address identity reviewed against the retailer/store-owned website; manufacturer directory names alone do not qualify.';
COMMENT ON COLUMN vkpi_dealers.physical_store_status IS
  'Independent human-reviewed physical-store fact. It is not product presence, brand authorization, inventory, sales, ROI or local impact.';
COMMENT ON COLUMN vkpi_dealers.google_place_verification_status IS
  'Optional Google Place cross-check only. pending is the default and Google never becomes the canonical address source.';
COMMENT ON COLUMN vkpi_dealers.google_place_evidence_json IS
  'Bounded Google Places cross-check receipt. Empty while pending; never inferred or fabricated when provider access is unavailable.';
