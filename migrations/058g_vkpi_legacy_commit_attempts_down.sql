DROP INDEX IF EXISTS idx_vkpi_legacy_committed_attempt;
DROP INDEX IF EXISTS uniq_committed_refs_attempt;

DELETE FROM vkpi_legacy_import_committed_refs older
USING vkpi_legacy_import_committed_refs newer
WHERE older.import_batch_id = newer.import_batch_id
  AND older.pipeline = newer.pipeline
  AND older.staging_table = newer.staging_table
  AND older.staging_id = newer.staging_id
  AND older.target_table = newer.target_table
  AND older.target_id = newer.target_id
  AND older.commit_attempt < newer.commit_attempt;

ALTER TABLE vkpi_legacy_import_committed_refs
  DROP COLUMN IF EXISTS commit_attempt;

ALTER TABLE vkpi_legacy_import_committed_refs
  ADD CONSTRAINT uniq_legacy_committed_refs_target
  UNIQUE(import_batch_id, pipeline, staging_table, staging_id, target_table, target_id);
