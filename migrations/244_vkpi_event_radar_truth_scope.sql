-- 244: Event Radar truth gates and workspace-safe identity.
--
-- IMPORTANT: forward migrations are executed inside connection.py's transaction
-- while it holds pg_advisory_xact_lock('viltrox_schema_migrations').  Do not add
-- BEGIN/COMMIT here: committing inside this file would release that lock before
-- the runner records schema_migrations, allowing two Gunicorn workers to race.
--
-- Existing rows stay in the legacy Viltrox workspace (organization_id=1).
-- Public watch targets stay shared; observations, decisions, relationships and
-- promotion receipts are workspace scoped.  This migration imports no data.

ALTER TABLE vkpi_event_source_runs
  ADD COLUMN IF NOT EXISTS organization_id BIGINT NOT NULL DEFAULT 1;
ALTER TABLE vkpi_event_opportunities
  ADD COLUMN IF NOT EXISTS organization_id BIGINT NOT NULL DEFAULT 1;
ALTER TABLE vkpi_event_source_observations
  ADD COLUMN IF NOT EXISTS organization_id BIGINT NOT NULL DEFAULT 1;
ALTER TABLE vkpi_event_opportunity_changes
  ADD COLUMN IF NOT EXISTS organization_id BIGINT NOT NULL DEFAULT 1;
ALTER TABLE vkpi_event_opportunity_dealers
  ADD COLUMN IF NOT EXISTS organization_id BIGINT NOT NULL DEFAULT 1;
ALTER TABLE vkpi_event_opportunity_promotions
  ADD COLUMN IF NOT EXISTS organization_id BIGINT NOT NULL DEFAULT 1;
ALTER TABLE vkpi_events
  ADD COLUMN IF NOT EXISTS organization_id BIGINT NOT NULL DEFAULT 1;

-- Replace migration 243's global identities with workspace identities.  The
-- child foreign keys are rebuilt first so the opportunity primary key can move
-- from (id) to (organization_id,id) without CASCADE.
ALTER TABLE vkpi_event_source_observations
  DROP CONSTRAINT IF EXISTS fk_event_observations_org_run;
ALTER TABLE vkpi_event_source_observations
  DROP CONSTRAINT IF EXISTS fk_event_observations_org_opportunity;
ALTER TABLE vkpi_event_opportunity_changes
  DROP CONSTRAINT IF EXISTS fk_event_changes_org_opportunity;
ALTER TABLE vkpi_event_opportunity_changes
  DROP CONSTRAINT IF EXISTS fk_event_changes_org_observation;
ALTER TABLE vkpi_event_opportunity_dealers
  DROP CONSTRAINT IF EXISTS fk_event_dealers_org_opportunity;
ALTER TABLE vkpi_event_opportunity_promotions
  DROP CONSTRAINT IF EXISTS fk_event_promotions_org_opportunity;
ALTER TABLE vkpi_event_opportunity_promotions
  DROP CONSTRAINT IF EXISTS fk_event_promotions_org_event;

ALTER TABLE vkpi_event_source_observations
  DROP CONSTRAINT IF EXISTS vkpi_event_source_observations_run_id_fkey;
ALTER TABLE vkpi_event_source_observations
  DROP CONSTRAINT IF EXISTS vkpi_event_source_observations_opportunity_id_fkey;
ALTER TABLE vkpi_event_opportunity_changes
  DROP CONSTRAINT IF EXISTS vkpi_event_opportunity_changes_opportunity_id_fkey;
ALTER TABLE vkpi_event_opportunity_changes
  DROP CONSTRAINT IF EXISTS vkpi_event_opportunity_changes_observation_id_fkey;
ALTER TABLE vkpi_event_opportunity_dealers
  DROP CONSTRAINT IF EXISTS vkpi_event_opportunity_dealers_opportunity_id_fkey;
ALTER TABLE vkpi_event_opportunity_promotions
  DROP CONSTRAINT IF EXISTS vkpi_event_opportunity_promotions_opportunity_id_fkey;
ALTER TABLE vkpi_event_opportunity_promotions
  DROP CONSTRAINT IF EXISTS vkpi_event_opportunity_promotions_event_id_fkey;

ALTER TABLE vkpi_event_source_runs
  DROP CONSTRAINT IF EXISTS vkpi_event_source_runs_run_key_key;
ALTER TABLE vkpi_event_source_runs
  DROP CONSTRAINT IF EXISTS chk_event_source_runs_organization;
ALTER TABLE vkpi_event_source_runs
  DROP CONSTRAINT IF EXISTS uq_event_source_runs_org_run_key;
ALTER TABLE vkpi_event_source_runs
  DROP CONSTRAINT IF EXISTS uq_event_source_runs_org_id;
ALTER TABLE vkpi_event_opportunities
  DROP CONSTRAINT IF EXISTS vkpi_event_opportunities_pkey;
ALTER TABLE vkpi_event_opportunities
  DROP CONSTRAINT IF EXISTS vkpi_event_opportunities_canonical_key_key;
ALTER TABLE vkpi_event_opportunities
  DROP CONSTRAINT IF EXISTS vkpi_event_opportunities_source_id_external_event_key_key;
ALTER TABLE vkpi_event_opportunities
  DROP CONSTRAINT IF EXISTS chk_event_opportunities_organization;
ALTER TABLE vkpi_event_opportunities
  DROP CONSTRAINT IF EXISTS uq_event_opportunities_org_canonical;
ALTER TABLE vkpi_event_opportunities
  DROP CONSTRAINT IF EXISTS uq_event_opportunities_org_source_external;
ALTER TABLE vkpi_event_source_observations
  DROP CONSTRAINT IF EXISTS chk_event_observations_organization;
ALTER TABLE vkpi_event_source_observations
  DROP CONSTRAINT IF EXISTS uq_event_observations_org_id;
ALTER TABLE vkpi_event_source_observations
  DROP CONSTRAINT IF EXISTS uq_event_observations_org_source_external_hash;
ALTER TABLE vkpi_event_opportunity_changes
  DROP CONSTRAINT IF EXISTS chk_event_changes_organization;
ALTER TABLE vkpi_event_opportunity_dealers
  DROP CONSTRAINT IF EXISTS vkpi_event_opportunity_dealers_pkey;
ALTER TABLE vkpi_event_opportunity_dealers
  DROP CONSTRAINT IF EXISTS chk_event_dealers_organization;
ALTER TABLE vkpi_event_opportunity_promotions
  DROP CONSTRAINT IF EXISTS vkpi_event_opportunity_promotions_pkey;
ALTER TABLE vkpi_event_opportunity_promotions
  DROP CONSTRAINT IF EXISTS vkpi_event_opportunity_promotions_event_id_key;
ALTER TABLE vkpi_event_opportunity_promotions
  DROP CONSTRAINT IF EXISTS chk_event_promotions_organization;
ALTER TABLE vkpi_event_opportunity_promotions
  DROP CONSTRAINT IF EXISTS uq_event_promotions_org_event;
ALTER TABLE vkpi_events
  DROP CONSTRAINT IF EXISTS chk_vkpi_events_organization;
ALTER TABLE vkpi_events
  DROP CONSTRAINT IF EXISTS uq_vkpi_events_org_id;

-- The observation natural-key constraint generated by migration 243 is longer
-- than PostgreSQL's identifier limit, so discover it by ordered columns rather
-- than relying on its truncated implementation name.
DO $migration$
DECLARE
  legacy_constraint TEXT;
BEGIN
  SELECT c.conname INTO legacy_constraint
  FROM pg_constraint c
  WHERE c.conrelid = 'vkpi_event_source_observations'::regclass
    AND c.contype = 'u'
    AND (
      SELECT array_agg(a.attname ORDER BY key_col.ordinality)
      FROM unnest(c.conkey) WITH ORDINALITY AS key_col(attnum, ordinality)
      JOIN pg_attribute a
        ON a.attrelid = c.conrelid AND a.attnum = key_col.attnum
    ) = ARRAY['source_id','external_event_key','content_hash']::name[]
  LIMIT 1;
  IF legacy_constraint IS NOT NULL THEN
    EXECUTE format(
      'ALTER TABLE vkpi_event_source_observations DROP CONSTRAINT %I',
      legacy_constraint
    );
  END IF;
END
$migration$;

-- Positive workspace identifiers fail closed instead of silently collapsing
-- invalid or missing tenant context into a shared row.
ALTER TABLE vkpi_event_source_runs
  ADD CONSTRAINT chk_event_source_runs_organization CHECK (organization_id > 0);
ALTER TABLE vkpi_event_opportunities
  ADD CONSTRAINT chk_event_opportunities_organization CHECK (organization_id > 0);
ALTER TABLE vkpi_event_source_observations
  ADD CONSTRAINT chk_event_observations_organization CHECK (organization_id > 0);
ALTER TABLE vkpi_event_opportunity_changes
  ADD CONSTRAINT chk_event_changes_organization CHECK (organization_id > 0);
ALTER TABLE vkpi_event_opportunity_dealers
  ADD CONSTRAINT chk_event_dealers_organization CHECK (organization_id > 0);
ALTER TABLE vkpi_event_opportunity_promotions
  ADD CONSTRAINT chk_event_promotions_organization CHECK (organization_id > 0);
ALTER TABLE vkpi_events
  ADD CONSTRAINT chk_vkpi_events_organization CHECK (organization_id > 0);

ALTER TABLE vkpi_event_source_runs
  ADD CONSTRAINT uq_event_source_runs_org_run_key UNIQUE (organization_id, run_key);
ALTER TABLE vkpi_event_source_runs
  ADD CONSTRAINT uq_event_source_runs_org_id UNIQUE (organization_id, id);

ALTER TABLE vkpi_event_opportunities
  ADD CONSTRAINT vkpi_event_opportunities_pkey PRIMARY KEY (organization_id, id);
ALTER TABLE vkpi_event_opportunities
  ADD CONSTRAINT uq_event_opportunities_org_canonical UNIQUE (organization_id, canonical_key);
ALTER TABLE vkpi_event_opportunities
  ADD CONSTRAINT uq_event_opportunities_org_source_external
  UNIQUE (organization_id, source_id, external_event_key);

ALTER TABLE vkpi_event_source_observations
  ADD CONSTRAINT uq_event_observations_org_id UNIQUE (organization_id, id);
ALTER TABLE vkpi_event_source_observations
  ADD CONSTRAINT uq_event_observations_org_source_external_hash
  UNIQUE (organization_id, source_id, external_event_key, content_hash);

ALTER TABLE vkpi_event_opportunity_dealers
  ADD CONSTRAINT vkpi_event_opportunity_dealers_pkey
  PRIMARY KEY (organization_id, opportunity_id, dealer_id, relation_type);
ALTER TABLE vkpi_event_opportunity_promotions
  ADD CONSTRAINT vkpi_event_opportunity_promotions_pkey
  PRIMARY KEY (organization_id, opportunity_id);
ALTER TABLE vkpi_event_opportunity_promotions
  ADD CONSTRAINT uq_event_promotions_org_event UNIQUE (organization_id, event_id);

-- vkpi_events keeps its legacy globally unique id for compatibility.  This
-- composite key additionally lets promotion receipts enforce same-workspace
-- references at the database boundary.
ALTER TABLE vkpi_events
  ADD CONSTRAINT uq_vkpi_events_org_id UNIQUE (organization_id, id);

ALTER TABLE vkpi_event_source_observations
  ADD CONSTRAINT fk_event_observations_org_run
  FOREIGN KEY (organization_id, run_id)
  REFERENCES vkpi_event_source_runs(organization_id, id)
  ON DELETE SET NULL (run_id);
ALTER TABLE vkpi_event_source_observations
  ADD CONSTRAINT fk_event_observations_org_opportunity
  FOREIGN KEY (organization_id, opportunity_id)
  REFERENCES vkpi_event_opportunities(organization_id, id)
  ON DELETE SET NULL (opportunity_id);
ALTER TABLE vkpi_event_opportunity_changes
  ADD CONSTRAINT fk_event_changes_org_opportunity
  FOREIGN KEY (organization_id, opportunity_id)
  REFERENCES vkpi_event_opportunities(organization_id, id)
  ON DELETE CASCADE;
ALTER TABLE vkpi_event_opportunity_changes
  ADD CONSTRAINT fk_event_changes_org_observation
  FOREIGN KEY (organization_id, observation_id)
  REFERENCES vkpi_event_source_observations(organization_id, id)
  ON DELETE SET NULL (observation_id);
ALTER TABLE vkpi_event_opportunity_dealers
  ADD CONSTRAINT fk_event_dealers_org_opportunity
  FOREIGN KEY (organization_id, opportunity_id)
  REFERENCES vkpi_event_opportunities(organization_id, id)
  ON DELETE CASCADE;
ALTER TABLE vkpi_event_opportunity_promotions
  ADD CONSTRAINT fk_event_promotions_org_opportunity
  FOREIGN KEY (organization_id, opportunity_id)
  REFERENCES vkpi_event_opportunities(organization_id, id)
  ON DELETE RESTRICT;
ALTER TABLE vkpi_event_opportunity_promotions
  ADD CONSTRAINT fk_event_promotions_org_event
  FOREIGN KEY (organization_id, event_id)
  REFERENCES vkpi_events(organization_id, id)
  ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_event_radar_org_date
  ON vkpi_event_opportunities(organization_id, start_date, lane);
CREATE INDEX IF NOT EXISTS idx_event_radar_org_decision_updated
  ON vkpi_event_opportunities(organization_id, decision_status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_event_radar_changes_org
  ON vkpi_event_opportunity_changes(organization_id, opportunity_id, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_event_radar_promotions_org
  ON vkpi_event_opportunity_promotions(organization_id, promoted_at DESC);
CREATE INDEX IF NOT EXISTS idx_event_source_runs_org
  ON vkpi_event_source_runs(organization_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_events_org_start
  ON vkpi_events(organization_id, start_date DESC);

-- Reviewed aliases map a public spelling/domain/social identity to one Dealer
-- row inside one workspace.  The same public alias may legitimately be reviewed
-- independently by another workspace; it cannot map twice inside one workspace.
CREATE TABLE IF NOT EXISTS vkpi_dealer_identity_aliases (
    id BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL DEFAULT 1,
    dealer_id BIGINT NOT NULL REFERENCES vkpi_dealers(id) ON DELETE CASCADE,
    stable_org_key TEXT NOT NULL,
    stable_location_key TEXT NOT NULL DEFAULT '',
    alias_type TEXT NOT NULL,
    alias_value TEXT NOT NULL,
    alias_normalized TEXT NOT NULL,
    country_code TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    evidence_grade TEXT NOT NULL DEFAULT 'A2',
    verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_dealer_identity_alias_org CHECK (organization_id > 0),
    CONSTRAINT chk_dealer_identity_alias_type CHECK (
      alias_type IN ('official_name','store_name','domain','social','event_host')
    ),
    CONSTRAINT chk_dealer_identity_alias_grade CHECK (evidence_grade IN ('A1','A2','B','X')),
    CONSTRAINT chk_dealer_identity_stable_org CHECK (stable_org_key LIKE 'dealer_org_%'),
    CONSTRAINT chk_dealer_identity_stable_location CHECK (
      stable_location_key = '' OR stable_location_key LIKE 'dealer_loc_%'
    ),
    CONSTRAINT uq_dealer_identity_alias_org
      UNIQUE(organization_id, alias_type, alias_normalized, country_code)
);

CREATE INDEX IF NOT EXISTS idx_dealer_identity_stable_org
  ON vkpi_dealer_identity_aliases(organization_id, stable_org_key);
CREATE INDEX IF NOT EXISTS idx_dealer_identity_stable_location
  ON vkpi_dealer_identity_aliases(organization_id, stable_location_key)
  WHERE stable_location_key <> '';

COMMENT ON TABLE vkpi_dealer_identity_aliases IS
  'Reviewed Dealer identity aliases only. Alias matching never proves Viltrox authorization, current inventory, event participation, sales, ROI or local impact.';
COMMENT ON COLUMN vkpi_event_opportunities.organization_id IS
  'Workspace scope for review and promotion. Legacy rows default to Viltrox organization 1.';
