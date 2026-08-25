-- 299: preserve structurally valid but semantically incomplete final_v1 output.
-- quality_incomplete is deliberately isolated from the legacy target_type='video'
-- namespace. A code-only rollback therefore cannot reinterpret a paid but
-- incomplete result as ready evidence.

ALTER TABLE vkpi_analysis_cache
  DROP CONSTRAINT IF EXISTS chk_vkpi_analysis_cache_status;

ALTER TABLE vkpi_analysis_cache
  DROP CONSTRAINT IF EXISTS chk_vkpi_analysis_cache_quality_namespace;

ALTER TABLE vkpi_analysis_cache
  ADD CONSTRAINT chk_vkpi_analysis_cache_status
  CHECK (status IN ('ready', 'stale', 'quality_incomplete'));

-- A previous candidate could have written quality_incomplete into the legacy
-- video namespace before this guard existed. Move those rows without changing
-- result, model, cost or ownership. If the isolated natural key is occupied,
-- retain both paid records by giving only the migrated legacy row a stable,
-- id-derived derive_method suffix. An already occupied suffix is unexpected;
-- fail the runner-owned transaction instead of overwriting either record.
DO $migration$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM vkpi_analysis_cache
    WHERE status='quality_incomplete'
      AND target_type NOT IN ('video', 'video_quality_triage')
  ) THEN
    RAISE EXCEPTION
      'migration 299 refuses quality_incomplete outside video namespaces'
      USING ERRCODE='check_violation';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM vkpi_analysis_cache AS source
    JOIN vkpi_analysis_cache AS natural_target
      ON natural_target.target_type='video_quality_triage'
     AND natural_target.target_id=source.target_id
     AND natural_target.derive_method=source.derive_method
    JOIN vkpi_analysis_cache AS suffix_target
      ON suffix_target.target_type='video_quality_triage'
     AND suffix_target.target_id=source.target_id
     AND suffix_target.derive_method=(
       source.derive_method || '__quality_migrated_' || source.id::text
     )
    WHERE source.status='quality_incomplete'
      AND source.target_type='video'
  ) THEN
    RAISE EXCEPTION
      'migration 299 cannot preserve a conflicting paid quality record safely'
      USING ERRCODE='unique_violation';
  END IF;

  UPDATE vkpi_analysis_cache AS source
  SET target_type='video_quality_triage',
      derive_method=CASE
        WHEN EXISTS (
          SELECT 1
          FROM vkpi_analysis_cache AS natural_target
          WHERE natural_target.target_type='video_quality_triage'
            AND natural_target.target_id=source.target_id
            AND natural_target.derive_method=source.derive_method
        )
        THEN source.derive_method || '__quality_migrated_' || source.id::text
        ELSE source.derive_method
      END
  WHERE source.status='quality_incomplete'
    AND source.target_type='video';
END
$migration$;

ALTER TABLE vkpi_analysis_cache
  ADD CONSTRAINT chk_vkpi_analysis_cache_quality_namespace
  CHECK (
    status <> 'quality_incomplete'
    OR target_type = 'video_quality_triage'
  ) NOT VALID;

ALTER TABLE vkpi_analysis_cache
  VALIDATE CONSTRAINT chk_vkpi_analysis_cache_quality_namespace;

COMMENT ON COLUMN vkpi_analysis_cache.status IS
  'ready is downstream-consumable, stale is expired, quality_incomplete is retained under an isolated quality-triage target namespace and excluded from legacy ready evidence reads.';
