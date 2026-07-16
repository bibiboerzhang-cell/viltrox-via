-- Roll back migration 244 to migration 243's single-workspace schema.
-- This down script is not part of the automatic forward manifest, so it owns
-- its transaction and removes the migration ledger marker itself.
BEGIN;

-- Dropping organization_id is only lossless while every scoped row still
-- belongs to the legacy workspace.  Once another workspace has data, restore
-- the pre-244 backup or write an explicit data-preserving reverse migration.
DO $rollback$
DECLARE
  scoped_table TEXT;
  nonlegacy_exists BOOLEAN;
  alias_rows_exist BOOLEAN;
BEGIN
  FOREACH scoped_table IN ARRAY ARRAY[
    'vkpi_event_source_runs',
    'vkpi_event_opportunities',
    'vkpi_event_source_observations',
    'vkpi_event_opportunity_changes',
    'vkpi_event_opportunity_dealers',
    'vkpi_event_opportunity_promotions',
    'vkpi_events'
  ]
  LOOP
    EXECUTE format(
      'SELECT EXISTS (SELECT 1 FROM %I WHERE organization_id <> 1)',
      scoped_table
    ) INTO nonlegacy_exists;
    IF nonlegacy_exists THEN
      RAISE EXCEPTION
        'migration 244 rollback refused: % contains non-legacy workspace rows',
        scoped_table;
    END IF;
  END LOOP;

  IF to_regclass('vkpi_dealer_identity_aliases') IS NOT NULL THEN
    SELECT EXISTS (SELECT 1 FROM vkpi_dealer_identity_aliases)
      INTO alias_rows_exist;
    IF alias_rows_exist THEN
      RAISE EXCEPTION
        'migration 244 rollback refused: export Dealer identity aliases first';
    END IF;
  END IF;
END
$rollback$;

DROP TABLE IF EXISTS vkpi_dealer_identity_aliases;

DROP INDEX IF EXISTS idx_vkpi_events_org_start;
DROP INDEX IF EXISTS idx_event_source_runs_org;
DROP INDEX IF EXISTS idx_event_radar_promotions_org;
DROP INDEX IF EXISTS idx_event_radar_changes_org;
DROP INDEX IF EXISTS idx_event_radar_org_decision_updated;
DROP INDEX IF EXISTS idx_event_radar_org_date;

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

ALTER TABLE vkpi_event_source_runs
  DROP CONSTRAINT IF EXISTS uq_event_source_runs_org_run_key;
ALTER TABLE vkpi_event_source_runs
  DROP CONSTRAINT IF EXISTS uq_event_source_runs_org_id;
ALTER TABLE vkpi_event_opportunities
  DROP CONSTRAINT IF EXISTS vkpi_event_opportunities_pkey;
ALTER TABLE vkpi_event_opportunities
  DROP CONSTRAINT IF EXISTS uq_event_opportunities_org_canonical;
ALTER TABLE vkpi_event_opportunities
  DROP CONSTRAINT IF EXISTS uq_event_opportunities_org_source_external;
ALTER TABLE vkpi_event_source_observations
  DROP CONSTRAINT IF EXISTS uq_event_observations_org_id;
ALTER TABLE vkpi_event_source_observations
  DROP CONSTRAINT IF EXISTS uq_event_observations_org_source_external_hash;
ALTER TABLE vkpi_event_opportunity_dealers
  DROP CONSTRAINT IF EXISTS vkpi_event_opportunity_dealers_pkey;
ALTER TABLE vkpi_event_opportunity_promotions
  DROP CONSTRAINT IF EXISTS vkpi_event_opportunity_promotions_pkey;
ALTER TABLE vkpi_event_opportunity_promotions
  DROP CONSTRAINT IF EXISTS uq_event_promotions_org_event;
ALTER TABLE vkpi_events
  DROP CONSTRAINT IF EXISTS uq_vkpi_events_org_id;

ALTER TABLE vkpi_event_source_runs
  ADD CONSTRAINT vkpi_event_source_runs_run_key_key UNIQUE (run_key);
ALTER TABLE vkpi_event_opportunities
  ADD CONSTRAINT vkpi_event_opportunities_pkey PRIMARY KEY (id);
ALTER TABLE vkpi_event_opportunities
  ADD CONSTRAINT vkpi_event_opportunities_canonical_key_key UNIQUE (canonical_key);
ALTER TABLE vkpi_event_opportunities
  ADD CONSTRAINT vkpi_event_opportunities_source_id_external_event_key_key
  UNIQUE (source_id, external_event_key);
ALTER TABLE vkpi_event_source_observations
  ADD CONSTRAINT vkpi_event_source_observations_source_external_hash_key
  UNIQUE (source_id, external_event_key, content_hash);
ALTER TABLE vkpi_event_opportunity_dealers
  ADD CONSTRAINT vkpi_event_opportunity_dealers_pkey
  PRIMARY KEY (opportunity_id, dealer_id, relation_type);
ALTER TABLE vkpi_event_opportunity_promotions
  ADD CONSTRAINT vkpi_event_opportunity_promotions_pkey PRIMARY KEY (opportunity_id);
ALTER TABLE vkpi_event_opportunity_promotions
  ADD CONSTRAINT vkpi_event_opportunity_promotions_event_id_key UNIQUE (event_id);

ALTER TABLE vkpi_event_source_observations
  ADD CONSTRAINT vkpi_event_source_observations_run_id_fkey
  FOREIGN KEY (run_id) REFERENCES vkpi_event_source_runs(id) ON DELETE SET NULL;
ALTER TABLE vkpi_event_source_observations
  ADD CONSTRAINT vkpi_event_source_observations_opportunity_id_fkey
  FOREIGN KEY (opportunity_id) REFERENCES vkpi_event_opportunities(id) ON DELETE SET NULL;
ALTER TABLE vkpi_event_opportunity_changes
  ADD CONSTRAINT vkpi_event_opportunity_changes_opportunity_id_fkey
  FOREIGN KEY (opportunity_id) REFERENCES vkpi_event_opportunities(id) ON DELETE CASCADE;
ALTER TABLE vkpi_event_opportunity_changes
  ADD CONSTRAINT vkpi_event_opportunity_changes_observation_id_fkey
  FOREIGN KEY (observation_id) REFERENCES vkpi_event_source_observations(id) ON DELETE SET NULL;
ALTER TABLE vkpi_event_opportunity_dealers
  ADD CONSTRAINT vkpi_event_opportunity_dealers_opportunity_id_fkey
  FOREIGN KEY (opportunity_id) REFERENCES vkpi_event_opportunities(id) ON DELETE CASCADE;
ALTER TABLE vkpi_event_opportunity_promotions
  ADD CONSTRAINT vkpi_event_opportunity_promotions_opportunity_id_fkey
  FOREIGN KEY (opportunity_id) REFERENCES vkpi_event_opportunities(id) ON DELETE RESTRICT;
ALTER TABLE vkpi_event_opportunity_promotions
  ADD CONSTRAINT vkpi_event_opportunity_promotions_event_id_fkey
  FOREIGN KEY (event_id) REFERENCES vkpi_events(id) ON DELETE RESTRICT;

ALTER TABLE vkpi_events
  DROP CONSTRAINT IF EXISTS chk_vkpi_events_organization;
ALTER TABLE vkpi_event_opportunity_promotions
  DROP CONSTRAINT IF EXISTS chk_event_promotions_organization;
ALTER TABLE vkpi_event_opportunity_dealers
  DROP CONSTRAINT IF EXISTS chk_event_dealers_organization;
ALTER TABLE vkpi_event_opportunity_changes
  DROP CONSTRAINT IF EXISTS chk_event_changes_organization;
ALTER TABLE vkpi_event_source_observations
  DROP CONSTRAINT IF EXISTS chk_event_observations_organization;
ALTER TABLE vkpi_event_opportunities
  DROP CONSTRAINT IF EXISTS chk_event_opportunities_organization;
ALTER TABLE vkpi_event_source_runs
  DROP CONSTRAINT IF EXISTS chk_event_source_runs_organization;

ALTER TABLE vkpi_events DROP COLUMN IF EXISTS organization_id;
ALTER TABLE vkpi_event_opportunity_promotions DROP COLUMN IF EXISTS organization_id;
ALTER TABLE vkpi_event_opportunity_dealers DROP COLUMN IF EXISTS organization_id;
ALTER TABLE vkpi_event_opportunity_changes DROP COLUMN IF EXISTS organization_id;
ALTER TABLE vkpi_event_source_observations DROP COLUMN IF EXISTS organization_id;
ALTER TABLE vkpi_event_opportunities DROP COLUMN IF EXISTS organization_id;
ALTER TABLE vkpi_event_source_runs DROP COLUMN IF EXISTS organization_id;

DELETE FROM schema_migrations
WHERE version_key = '244_vkpi_event_radar_truth_scope.sql';

COMMIT;
