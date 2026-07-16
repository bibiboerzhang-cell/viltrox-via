-- Roll back migration 248 only after exporting source-passport evidence.
-- This removes audit history and field-level provenance, but never edits the
-- existing Dealer/Event business rows.
BEGIN;

DROP TABLE IF EXISTS vkpi_source_passport_revisions;
DROP TABLE IF EXISTS vkpi_source_field_evidence;
DROP TABLE IF EXISTS vkpi_source_passports;

DELETE FROM schema_migrations
WHERE version_key = '248_vkpi_dealer_event_source_passports.sql';

COMMIT;
