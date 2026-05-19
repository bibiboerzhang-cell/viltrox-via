-- P2D commit attempt history for legacy KOL pool writes.
--
-- Earlier rollback -> recommit drills preserved rolled-back insert refs but
-- could overwrite rolled-back update refs because update target ids stay fixed.
-- commit_attempt makes every P2D write attempt independently auditable.

ALTER TABLE vkpi_legacy_import_committed_refs
  ADD COLUMN IF NOT EXISTS commit_attempt INTEGER NOT NULL DEFAULT 1;

DO $$
DECLARE
  old_constraint_name TEXT;
BEGIN
  SELECT c.conname
  INTO old_constraint_name
  FROM pg_constraint c
  JOIN pg_class t ON t.oid = c.conrelid
  WHERE t.relname = 'vkpi_legacy_import_committed_refs'
    AND c.contype = 'u'
    AND (
      SELECT array_agg(a.attname::TEXT ORDER BY cols.ord)
      FROM unnest(c.conkey) WITH ORDINALITY AS cols(attnum, ord)
      JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = cols.attnum
    ) = ARRAY[
      'import_batch_id',
      'pipeline',
      'staging_table',
      'staging_id',
      'target_table',
      'target_id'
    ]::TEXT[];

  IF old_constraint_name IS NOT NULL THEN
    EXECUTE format(
      'ALTER TABLE vkpi_legacy_import_committed_refs DROP CONSTRAINT %I',
      old_constraint_name
    );
  END IF;
END $$;

WITH rollback_batches AS (
  SELECT import_batch_id
  FROM vkpi_legacy_import_committed_refs
  GROUP BY import_batch_id
  HAVING COUNT(*) FILTER (WHERE rollback_status = 'rolled_back') > 0
     AND COUNT(*) FILTER (WHERE rollback_status = 'not_rolled_back') > 0
)
UPDATE vkpi_legacy_import_committed_refs refs
SET commit_attempt = 2
FROM rollback_batches rb
WHERE refs.import_batch_id = rb.import_batch_id
  AND refs.rollback_status = 'not_rolled_back';

INSERT INTO vkpi_legacy_import_committed_refs (
  import_batch_id,
  pipeline,
  staging_table,
  staging_id,
  target_table,
  target_id,
  commit_action,
  previous_snapshot_json,
  new_snapshot_json,
  rollback_status,
  committed_by_staff_id,
  rolled_back_by_staff_id,
  committed_at,
  rolled_back_at,
  metadata_json,
  commit_attempt
)
SELECT
  refs.import_batch_id,
  refs.pipeline,
  refs.staging_table,
  refs.staging_id,
  refs.target_table,
  refs.target_id,
  refs.commit_action,
  refs.previous_snapshot_json,
  refs.new_snapshot_json,
  'rolled_back',
  refs.committed_by_staff_id,
  batches.rolled_back_by_staff_id,
  refs.committed_at,
  COALESCE(batches.rolled_back_at, refs.rolled_back_at),
  refs.metadata_json,
  1
FROM vkpi_legacy_import_committed_refs refs
JOIN vkpi_legacy_import_batches batches
  ON batches.id = refs.import_batch_id
WHERE refs.commit_attempt = 2
  AND refs.rollback_status = 'not_rolled_back'
  AND refs.commit_action = 'update'
  AND EXISTS (
    SELECT 1
    FROM vkpi_legacy_import_committed_refs rolled
    WHERE rolled.import_batch_id = refs.import_batch_id
      AND rolled.commit_attempt = 1
      AND rolled.rollback_status = 'rolled_back'
  )
  AND NOT EXISTS (
    SELECT 1
    FROM vkpi_legacy_import_committed_refs existing
    WHERE existing.import_batch_id = refs.import_batch_id
      AND existing.commit_attempt = 1
      AND existing.pipeline = refs.pipeline
      AND existing.staging_table = refs.staging_table
      AND existing.staging_id = refs.staging_id
      AND existing.target_table = refs.target_table
      AND existing.target_id = refs.target_id
  );

CREATE UNIQUE INDEX IF NOT EXISTS uniq_committed_refs_attempt
  ON vkpi_legacy_import_committed_refs (
    import_batch_id,
    commit_attempt,
    pipeline,
    staging_table,
    staging_id,
    target_table,
    target_id
  );

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_committed_attempt
  ON vkpi_legacy_import_committed_refs(import_batch_id, commit_attempt, rollback_status);
