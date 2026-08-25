-- Roll back migration 299. Preserve every incomplete paid execution as stale
-- evidence; never promote it to ready and never delete it. The namespace stays
-- isolated from target_type='video', so old code cannot see it as ready output.

UPDATE vkpi_analysis_cache
SET status='stale'
WHERE status='quality_incomplete';

ALTER TABLE vkpi_analysis_cache
  DROP CONSTRAINT IF EXISTS chk_vkpi_analysis_cache_quality_namespace;

ALTER TABLE vkpi_analysis_cache
  DROP CONSTRAINT IF EXISTS chk_vkpi_analysis_cache_status;

ALTER TABLE vkpi_analysis_cache
  ADD CONSTRAINT chk_vkpi_analysis_cache_status
  CHECK (status IN ('ready', 'stale'));

COMMENT ON COLUMN vkpi_analysis_cache.status IS
  'ready is downstream-consumable and stale is expired or retained rollback evidence.';

DELETE FROM schema_migrations
WHERE version_key='299_vkpi_analysis_cache_quality_incomplete.sql';
