-- 271: Dealer web verification history (official-site liveness, camera-retail
-- confirmation, Viltrox shelf presence and prominence tiering).
--
-- Each run appends one immutable receipt row per dealer; the read side always
-- takes the latest row per dealer_id (MAX(verified_at) join).  History is kept
-- so a store that dies or drops Viltrox leaves an auditable trail.
--
-- Truth boundaries (mirrors migration 270 discipline):
-- * carries_viltrox = 'confirmed' proves one retailer-owned page showed a
--   Viltrox product at verified_at.  It is NOT current inventory, sales,
--   authorization, or ROI.  Viltrox authorization stays in
--   vkpi_dealers.authorization_status and is never written here.
-- * prominence_score is a bounded editorial-style ranking signal derived from
--   public evidence (chain footprint, media mentions, review volume).  It is
--   descriptive only and never feeds viltrox_fit_score or any KOL scoring.
-- * This table never mutates vkpi_dealers; publication and map eligibility
--   are untouched.

CREATE TABLE IF NOT EXISTS vkpi_dealer_web_verification (
  dealer_id            BIGINT NOT NULL REFERENCES vkpi_dealers(id) ON DELETE CASCADE,
  verified_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  website_status       TEXT NOT NULL DEFAULT 'unreachable',
  is_camera_retailer   BOOLEAN,
  carries_viltrox      TEXT NOT NULL DEFAULT 'unknown',
  viltrox_evidence_url TEXT,
  scale_tier           TEXT NOT NULL DEFAULT 'unknown',
  prominence_score     SMALLINT,
  prominence_rationale TEXT NOT NULL DEFAULT '',
  evidence_json        JSONB NOT NULL DEFAULT '{}'::jsonb,
  model                TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (dealer_id, verified_at)
);

ALTER TABLE vkpi_dealer_web_verification
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_webverify_website_status,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_webverify_carries_viltrox,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_webverify_scale_tier,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_webverify_prominence_range,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_webverify_evidence_object,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_webverify_confirmed_receipt,
  DROP CONSTRAINT IF EXISTS chk_vkpi_dealer_webverify_dead_site_shape;

ALTER TABLE vkpi_dealer_web_verification
  ADD CONSTRAINT chk_vkpi_dealer_webverify_website_status CHECK (
    website_status IN ('alive', 'dead', 'unreachable')
  ),
  ADD CONSTRAINT chk_vkpi_dealer_webverify_carries_viltrox CHECK (
    carries_viltrox IN ('confirmed', 'not_found', 'unknown')
  ),
  ADD CONSTRAINT chk_vkpi_dealer_webverify_scale_tier CHECK (
    scale_tier IN (
      'national_chain', 'regional_chain', 'local_store', 'online_focus', 'unknown'
    )
  ),
  ADD CONSTRAINT chk_vkpi_dealer_webverify_prominence_range CHECK (
    prominence_score IS NULL
    OR (prominence_score >= 0 AND prominence_score <= 100)
  ),
  ADD CONSTRAINT chk_vkpi_dealer_webverify_evidence_object CHECK (
    jsonb_typeof(evidence_json) = 'object'
  ),
  -- Fail closed: a confirmed Viltrox shelf claim without a concrete public
  -- evidence URL can never be stored.  The URL must be a plain https page and
  -- never a search-provider redirect host.
  ADD CONSTRAINT chk_vkpi_dealer_webverify_confirmed_receipt CHECK (
    carries_viltrox <> 'confirmed'
    OR (
      NULLIF(TRIM(viltrox_evidence_url), '') IS NOT NULL
      AND viltrox_evidence_url ~* '^https://[^[:space:]]+$'
      AND viltrox_evidence_url !~* '^https://([^/]+\.)?vertexaisearch\.cloud\.google\.com(/|$)'
    )
  ),
  -- A site judged dead or unreachable cannot simultaneously claim a live
  -- camera-retail storefront or a confirmed Viltrox shelf.
  ADD CONSTRAINT chk_vkpi_dealer_webverify_dead_site_shape CHECK (
    website_status = 'alive'
    OR (
      carries_viltrox <> 'confirmed'
      AND is_camera_retailer IS DISTINCT FROM TRUE
    )
  );

-- Latest-row lookup per dealer (read side does MAX(verified_at) GROUP BY).
CREATE INDEX IF NOT EXISTS idx_vkpi_dealer_webverify_latest
  ON vkpi_dealer_web_verification(dealer_id, verified_at DESC);

-- Ranking scan: prominence descending across the most recent receipts.
CREATE INDEX IF NOT EXISTS idx_vkpi_dealer_webverify_prominence
  ON vkpi_dealer_web_verification(prominence_score DESC, verified_at DESC);

-- Viltrox shelf filter for the rankings module badge counts.
CREATE INDEX IF NOT EXISTS idx_vkpi_dealer_webverify_carries
  ON vkpi_dealer_web_verification(carries_viltrox, verified_at DESC);

COMMENT ON TABLE vkpi_dealer_web_verification IS
  'Append-only web-verification receipts per dealer: site liveness, camera-retail fact, Viltrox shelf evidence and public prominence tier. Read side always joins the latest row per dealer_id.';
COMMENT ON COLUMN vkpi_dealer_web_verification.website_status IS
  'alive = official site loads and represents the business; dead = site gone or business closed per its own pages; unreachable = could not be verified this run.';
COMMENT ON COLUMN vkpi_dealer_web_verification.is_camera_retailer IS
  'TRUE only when the retailer-owned site currently shows camera or photo gear for sale. NULL = not determinable this run.';
COMMENT ON COLUMN vkpi_dealer_web_verification.carries_viltrox IS
  'confirmed requires viltrox_evidence_url (retailer-owned product or brand page). not_found = searched and absent. unknown = could not verify. Never proves inventory, sales or authorization.';
COMMENT ON COLUMN vkpi_dealer_web_verification.scale_tier IS
  'national_chain / regional_chain / local_store / online_focus / unknown, judged from the retailer-owned store-locator plus public sources.';
COMMENT ON COLUMN vkpi_dealer_web_verification.prominence_score IS
  'Bounded 0-100 public-prominence signal (chain footprint, media reputation, local review volume). Descriptive ranking aid only; never feeds fit scoring.';
COMMENT ON COLUMN vkpi_dealer_web_verification.evidence_json IS
  'Bounded receipt: evidence_urls, grounding_urls, store_count_estimate, notes, shared_from_host when a sibling location reused a same-domain verdict.';
COMMENT ON COLUMN vkpi_dealer_web_verification.model IS
  'Verification channel identifier recorded for audit; empty string only for legacy or manual rows.';
