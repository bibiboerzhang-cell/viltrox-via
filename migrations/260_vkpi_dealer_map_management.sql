-- 260: Dealer map management, multi-brand evidence and future activity hooks.
--
-- This migration is additive.  It does not create Dealer records, infer a
-- manufacturer authorization, assert Viltrox inventory, or convert a retailer
-- product page into an authorization claim.  Existing migration-259 reviewed
-- map rows keep their current visibility through the bounded backfill below;
-- all other existing and future rows remain drafts until a manager publishes
-- them explicitly.

ALTER TABLE vkpi_dealers
  ADD COLUMN IF NOT EXISTS publication_status TEXT NOT NULL DEFAULT 'draft',
  ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS published_by TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS viltrox_deployment_status TEXT NOT NULL DEFAULT 'not_deployed',
  ADD COLUMN IF NOT EXISTS viltrox_deployed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS viltrox_deployed_by TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS viltrox_deployment_note TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS activity_status TEXT NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS activity_page_url TEXT,
  ADD COLUMN IF NOT EXISTS activity_checked_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS next_activity_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS activity_note TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS website_url TEXT,
  ADD COLUMN IF NOT EXISTS social_links_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE vkpi_dealers
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_publication_status;
ALTER TABLE vkpi_dealers
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_published_receipt;
ALTER TABLE vkpi_dealers
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_viltrox_deployment_status;
ALTER TABLE vkpi_dealers
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_viltrox_deployed_receipt;
ALTER TABLE vkpi_dealers
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_activity_status;
ALTER TABLE vkpi_dealers
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_website_url;
ALTER TABLE vkpi_dealers
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_social_links_array;

ALTER TABLE vkpi_dealers
  ADD CONSTRAINT chk_vkpi_dealer_publication_status CHECK (
    publication_status IN ('draft', 'published')
  ),
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
  ),
  ADD CONSTRAINT chk_vkpi_dealer_viltrox_deployment_status CHECK (
    viltrox_deployment_status IN ('not_deployed', 'planned', 'deployed', 'paused')
  ),
  ADD CONSTRAINT chk_vkpi_dealer_viltrox_deployed_receipt CHECK (
    viltrox_deployment_status NOT IN ('deployed', 'paused')
    OR (
      viltrox_deployed_at IS NOT NULL
      AND viltrox_deployed_by ~ '^staff_[1-9][0-9]{0,18}$'
    )
  ),
  ADD CONSTRAINT chk_vkpi_dealer_activity_status CHECK (
    activity_status IN ('unknown', 'none_observed', 'active')
  ),
  ADD CONSTRAINT chk_vkpi_dealer_website_url CHECK (
    website_url IS NULL OR website_url ~* '^https?://[^[:space:]]+$'
  ),
  ADD CONSTRAINT chk_vkpi_dealer_social_links_array CHECK (
    jsonb_typeof(social_links_json) = 'array'
    AND jsonb_array_length(social_links_json) <= 12
  );

CREATE TABLE IF NOT EXISTS vkpi_dealer_brand_relationships (
  dealer_id BIGINT NOT NULL REFERENCES vkpi_dealers(id) ON DELETE CASCADE,
  brand_key TEXT NOT NULL,
  relationship_status TEXT NOT NULL DEFAULT 'unverified',
  authorization_status TEXT NOT NULL DEFAULT 'unverified',
  evidence_url TEXT,
  source_checked_at TIMESTAMPTZ,
  reviewer_id TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (dealer_id, brand_key),
  CONSTRAINT chk_vkpi_dealer_brand_key CHECK (
    brand_key ~ '^[a-z0-9][a-z0-9_-]{1,63}$'
  ),
  CONSTRAINT chk_vkpi_dealer_brand_relationship_status CHECK (
    relationship_status IN (
      'unverified', 'declared', 'retailer_observed', 'official_directory_listed'
    )
  ),
  CONSTRAINT chk_vkpi_dealer_brand_authorization_status CHECK (
    authorization_status = 'unverified'
  )
);

-- Preserve visibility for legacy geocoded rows that were already on
-- the reviewed-source map before migration 259 introduced durable receipts.
-- Their reviewer_id remains empty and review_contract_version remains 0: the
-- distinct system actor records only a migration-compatibility publication,
-- never human review, brand authorization, product presence or inventory.
UPDATE vkpi_dealers
SET publication_status = 'published',
    published_at = COALESCE(reviewed_at, source_checked_at, created_at),
    published_by = CASE
      WHEN reviewer_id ~ '^staff_[1-9][0-9]{0,18}$' THEN reviewer_id
      ELSE 'system:migration260_legacy_visibility'
    END,
    updated_at = NOW()
WHERE publication_status = 'draft'
  AND source_status = 'public_listing_verified'
  AND lat IS NOT NULL
  AND lng IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_vkpi_dealers_publication
  ON vkpi_dealers(publication_status, state, city);
CREATE INDEX IF NOT EXISTS idx_vkpi_dealers_publication_geo
  ON vkpi_dealers(publication_status, lat, lng)
  WHERE lat IS NOT NULL AND lng IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_vkpi_dealer_brand_lookup
  ON vkpi_dealer_brand_relationships(brand_key, dealer_id);
CREATE INDEX IF NOT EXISTS idx_vkpi_dealer_activity_watch
  ON vkpi_dealers(activity_status, next_activity_at)
  WHERE publication_status = 'published';

COMMENT ON COLUMN vkpi_dealers.publication_status IS
  'Manager-controlled map visibility. Published manual rows remain explicitly unverified until separate evidence review.';
COMMENT ON COLUMN vkpi_dealers.viltrox_deployment_status IS
  'Internal Viltrox rollout status only; never authorization, product presence, inventory, sales, ROI or local-impact evidence.';
COMMENT ON COLUMN vkpi_dealers.activity_page_url IS
  'Optional public Dealer activity/calendar page for later Event Radar enrichment; no activity is inferred from its presence.';
COMMENT ON COLUMN vkpi_dealers.website_url IS
  'Manager-recorded public website URL; descriptive only and never authorization, inventory, sales or impact evidence.';
COMMENT ON COLUMN vkpi_dealers.social_links_json IS
  'Bounded public social profile links (maximum 12); descriptive only and never audience, authorization, sales or impact evidence.';
COMMENT ON TABLE vkpi_dealer_brand_relationships IS
  'Per-brand Dealer relationship evidence. Nikon/Sony/Canon or other manufacturer evidence never implies Viltrox authorization or inventory.';
