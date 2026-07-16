-- Roll back migration 257.  No candidate or source-registry passport row is
-- deleted implicitly: rollback fails closed if such reviewed rows exist.

DROP TRIGGER IF EXISTS trg_vkpi_candidate_promotion_gate
  ON vkpi_dealer_event_candidates;
DROP FUNCTION IF EXISTS vkpi_validate_candidate_promotion_gate();
DROP TABLE IF EXISTS vkpi_candidate_field_evidence_links;
DROP TABLE IF EXISTS vkpi_dealer_event_candidates;

DO $rollback_guard$
BEGIN
  IF EXISTS (
    SELECT 1 FROM vkpi_source_passports
    WHERE entity_type = 'source_registry' OR registry_source_id IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'cannot roll back 257 while source_registry passports exist';
  END IF;
END
$rollback_guard$;

DROP INDEX IF EXISTS uq_source_passport_registry_source;
ALTER TABLE vkpi_source_passports
  DROP CONSTRAINT IF EXISTS chk_source_passport_entity_type;
ALTER TABLE vkpi_source_passports
  DROP CONSTRAINT IF EXISTS chk_source_passport_entity_link;
ALTER TABLE vkpi_source_passports
  DROP CONSTRAINT IF EXISTS chk_source_passport_registry_id;
ALTER TABLE vkpi_source_passports
  DROP COLUMN IF EXISTS registry_source_id;

ALTER TABLE vkpi_source_passports
  ADD CONSTRAINT chk_source_passport_entity_type CHECK (
    entity_type IN ('dealer_location','event_source','event_opportunity')
  );
ALTER TABLE vkpi_source_passports
  ADD CONSTRAINT chk_source_passport_entity_link CHECK (
    (entity_type = 'dealer_location' AND dealer_id IS NOT NULL
      AND event_source_id IS NULL AND event_opportunity_id IS NULL)
    OR
    (entity_type = 'event_source' AND dealer_id IS NULL
      AND event_source_id IS NOT NULL AND event_opportunity_id IS NULL)
    OR
    (entity_type = 'event_opportunity' AND dealer_id IS NULL
      AND event_source_id IS NULL AND event_opportunity_id IS NOT NULL)
  );

DELETE FROM schema_migrations
WHERE version_key = '257_vkpi_dealer_event_candidate_staging.sql';
