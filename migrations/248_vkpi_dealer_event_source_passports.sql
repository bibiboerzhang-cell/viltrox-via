-- 248: durable Dealer/Event publisher passports and field-level provenance.
--
-- The migration runner owns the surrounding transaction and advisory lock.
-- Do not add BEGIN/COMMIT here.  This schema stores reviewed public-source
-- evidence only.  It cannot establish global coverage, Viltrox authorization,
-- current inventory, sales, ROI, attendance, or local commercial impact.

CREATE TABLE IF NOT EXISTS vkpi_source_passports (
    organization_id BIGINT NOT NULL,
    id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    dealer_id BIGINT REFERENCES vkpi_dealers(id) ON DELETE RESTRICT,
    event_source_id TEXT REFERENCES vkpi_event_watch_targets(id) ON DELETE RESTRICT,
    event_opportunity_id TEXT,
    stable_org_key TEXT NOT NULL DEFAULT '',
    exact_location_key TEXT NOT NULL DEFAULT '',
    publisher_name TEXT NOT NULL DEFAULT '',
    publisher_tier TEXT NOT NULL DEFAULT 'unknown',
    canonical_url TEXT NOT NULL DEFAULT '',
    identity_status TEXT NOT NULL DEFAULT 'unknown',
    verification_status TEXT NOT NULL DEFAULT 'unknown',
    freshness_status_at_write TEXT NOT NULL DEFAULT 'unavailable',
    verified_at TIMESTAMPTZ,
    stale_after_days INTEGER NOT NULL DEFAULT 30,
    reviewer_staff_id BIGINT REFERENCES staff(id) ON DELETE RESTRICT,
    claim_status TEXT NOT NULL DEFAULT 'descriptive_only',
    identity_evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    record_sha256 TEXT NOT NULL,
    revision_no INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (organization_id, id),
    CONSTRAINT chk_source_passport_org CHECK (organization_id > 0),
    CONSTRAINT chk_source_passport_entity_type CHECK (
      entity_type IN ('dealer_location','event_source','event_opportunity')
    ),
    CONSTRAINT chk_source_passport_entity_key CHECK (
      entity_key <> '' AND length(entity_key) <= 160
    ),
    CONSTRAINT chk_source_passport_entity_link CHECK (
      (entity_type = 'dealer_location' AND dealer_id IS NOT NULL
        AND event_source_id IS NULL AND event_opportunity_id IS NULL)
      OR
      (entity_type = 'event_source' AND dealer_id IS NULL
        AND event_source_id IS NOT NULL AND event_opportunity_id IS NULL)
      OR
      (entity_type = 'event_opportunity' AND dealer_id IS NULL
        AND event_source_id IS NULL AND event_opportunity_id IS NOT NULL)
    ),
    CONSTRAINT chk_source_passport_stable_org CHECK (
      stable_org_key = '' OR stable_org_key ~ '^dealer_org_[a-z0-9]{8,64}$'
    ),
    CONSTRAINT chk_source_passport_location CHECK (
      exact_location_key = '' OR exact_location_key ~ '^dealer_loc_[a-z0-9]{8,64}$'
    ),
    CONSTRAINT chk_source_passport_exact_location CHECK (
      entity_type <> 'dealer_location' OR identity_status <> 'exact'
      OR (stable_org_key <> '' AND exact_location_key <> '')
    ),
    CONSTRAINT chk_source_passport_exact_evidence CHECK (
      identity_status <> 'exact'
      OR (
        publisher_tier <> 'unknown'
        AND canonical_url LIKE 'https://%'
        AND verified_at IS NOT NULL
        AND reviewer_staff_id IS NOT NULL
        AND freshness_status_at_write = 'fresh'
      )
    ),
    CONSTRAINT chk_source_passport_publisher_tier CHECK (
      publisher_tier IN (
        'organizer_owned','retailer_owned','venue_owned','brand_owned',
        'platform_hosted_profile','third_party_listing','unknown'
      )
    ),
    CONSTRAINT chk_source_passport_identity CHECK (
      identity_status IN ('unknown','unresolved','exact','conflict')
    ),
    CONSTRAINT chk_source_passport_verification CHECK (
      verification_status IN ('unknown','observed','verified','rejected','needs_review')
    ),
    CONSTRAINT chk_source_passport_freshness CHECK (
      freshness_status_at_write IN ('unavailable','fresh','stale','invalid_future')
    ),
    CONSTRAINT chk_source_passport_stale_days CHECK (stale_after_days BETWEEN 1 AND 3650),
    CONSTRAINT chk_source_passport_claim CHECK (claim_status = 'descriptive_only'),
    CONSTRAINT chk_source_passport_sha CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_source_passport_revision CHECK (revision_no > 0),
    CONSTRAINT chk_source_passport_verified CHECK (
      verification_status <> 'verified'
      OR (
        identity_status = 'exact'
        AND publisher_tier <> 'unknown'
        AND canonical_url LIKE 'https://%'
        AND verified_at IS NOT NULL
        AND reviewer_staff_id IS NOT NULL
        AND freshness_status_at_write = 'fresh'
      )
    ),
    CONSTRAINT fk_source_passport_event_opportunity
      FOREIGN KEY (organization_id, event_opportunity_id)
      REFERENCES vkpi_event_opportunities(organization_id, id)
      ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_source_passport_entity
  ON vkpi_source_passports(organization_id, entity_type, entity_key);
CREATE UNIQUE INDEX IF NOT EXISTS uq_source_passport_dealer
  ON vkpi_source_passports(organization_id, dealer_id)
  WHERE entity_type = 'dealer_location';
CREATE UNIQUE INDEX IF NOT EXISTS uq_source_passport_event_source
  ON vkpi_source_passports(organization_id, event_source_id)
  WHERE entity_type = 'event_source';
CREATE UNIQUE INDEX IF NOT EXISTS uq_source_passport_event_opportunity
  ON vkpi_source_passports(organization_id, event_opportunity_id)
  WHERE entity_type = 'event_opportunity';
CREATE INDEX IF NOT EXISTS idx_source_passport_review
  ON vkpi_source_passports(
    organization_id, verification_status, freshness_status_at_write, updated_at DESC
  );
CREATE INDEX IF NOT EXISTS idx_source_passport_exact_location
  ON vkpi_source_passports(organization_id, exact_location_key)
  WHERE exact_location_key <> '';

CREATE TABLE IF NOT EXISTS vkpi_source_field_evidence (
    organization_id BIGINT NOT NULL,
    id TEXT NOT NULL,
    passport_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    value_sha256 TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    publisher_tier TEXT NOT NULL DEFAULT 'unknown',
    evidence_scope TEXT NOT NULL DEFAULT '',
    value_status TEXT NOT NULL DEFAULT 'unknown',
    verification_status TEXT NOT NULL DEFAULT 'unknown',
    freshness_status_at_write TEXT NOT NULL DEFAULT 'unavailable',
    observed_at TIMESTAMPTZ,
    verified_at TIMESTAMPTZ,
    stale_after_days INTEGER NOT NULL DEFAULT 30,
    reviewer_staff_id BIGINT REFERENCES staff(id) ON DELETE RESTRICT,
    claim_status TEXT NOT NULL DEFAULT 'descriptive_only',
    evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    record_sha256 TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (organization_id, id),
    CONSTRAINT fk_source_field_passport
      FOREIGN KEY (organization_id, passport_id)
      REFERENCES vkpi_source_passports(organization_id, id)
      ON DELETE RESTRICT,
    CONSTRAINT chk_source_field_org CHECK (organization_id > 0),
    CONSTRAINT chk_source_field_name CHECK (
      field_name ~ '^[a-z][a-z0-9_.]{1,119}$'
    ),
    CONSTRAINT chk_source_field_value_sha CHECK (
      value_sha256 = '' OR value_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT chk_source_field_publisher_tier CHECK (
      publisher_tier IN (
        'organizer_owned','retailer_owned','venue_owned','brand_owned',
        'platform_hosted_profile','third_party_listing','unknown'
      )
    ),
    CONSTRAINT chk_source_field_value_status CHECK (
      value_status IN ('unknown','observed','not_found','unavailable','conflict')
    ),
    CONSTRAINT chk_source_field_verification CHECK (
      verification_status IN ('unknown','observed','verified','rejected','needs_review')
    ),
    CONSTRAINT chk_source_field_freshness CHECK (
      freshness_status_at_write IN ('unavailable','fresh','stale','invalid_future')
    ),
    CONSTRAINT chk_source_field_stale_days CHECK (stale_after_days BETWEEN 1 AND 3650),
    CONSTRAINT chk_source_field_claim CHECK (claim_status = 'descriptive_only'),
    CONSTRAINT chk_source_field_record_sha CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_source_field_observed_value CHECK (
      value_status <> 'observed' OR value_sha256 <> ''
    ),
    CONSTRAINT chk_source_field_verified CHECK (
      verification_status <> 'verified'
      OR (
        value_status IN ('observed','not_found')
        AND publisher_tier <> 'unknown'
        AND source_url LIKE 'https://%'
        AND verified_at IS NOT NULL
        AND reviewer_staff_id IS NOT NULL
        AND freshness_status_at_write = 'fresh'
      )
    )
);

CREATE INDEX IF NOT EXISTS idx_source_field_passport
  ON vkpi_source_field_evidence(organization_id, passport_id, field_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_source_field_review
  ON vkpi_source_field_evidence(
    organization_id, verification_status, freshness_status_at_write, created_at DESC
  );

CREATE TABLE IF NOT EXISTS vkpi_source_passport_revisions (
    id BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL,
    passport_id TEXT NOT NULL,
    revision_no INTEGER NOT NULL,
    snapshot_sha256 TEXT NOT NULL,
    snapshot_json JSONB NOT NULL,
    changed_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
    reviewer_staff_id BIGINT REFERENCES staff(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_source_passport_revision
      FOREIGN KEY (organization_id, passport_id)
      REFERENCES vkpi_source_passports(organization_id, id)
      ON DELETE RESTRICT,
    CONSTRAINT chk_source_passport_revision_org CHECK (organization_id > 0),
    CONSTRAINT chk_source_passport_revision_no CHECK (revision_no > 0),
    CONSTRAINT chk_source_passport_revision_sha CHECK (
      snapshot_sha256 ~ '^[0-9a-f]{64}$'
    ),
    UNIQUE (organization_id, passport_id, revision_no)
);

CREATE INDEX IF NOT EXISTS idx_source_passport_revision_history
  ON vkpi_source_passport_revisions(organization_id, passport_id, revision_no DESC);

COMMENT ON TABLE vkpi_source_passports IS
  'Workspace-scoped publisher identity passports for exact Dealer/Event entities. Every row remains descriptive_only and never proves global coverage or business outcomes.';
COMMENT ON COLUMN vkpi_source_passports.freshness_status_at_write IS
  'Freshness evaluated when the passport revision was written. Readers must recompute freshness from verified_at; this value is not permanently current.';
COMMENT ON COLUMN vkpi_source_passports.exact_location_key IS
  'Human-reviewed exact Dealer location key. Empty means unresolved; fuzzy matching must never populate it automatically.';
COMMENT ON TABLE vkpi_source_field_evidence IS
  'Append-only field-level provenance. value_sha256 links evidence to a value without duplicating raw contact or business data.';
COMMENT ON TABLE vkpi_source_passport_revisions IS
  'Append-only passport revision snapshots for audit history; not a global-source coverage ledger.';
