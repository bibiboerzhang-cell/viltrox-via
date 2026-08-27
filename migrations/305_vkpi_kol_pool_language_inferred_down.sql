-- Roll back only migration-305 rebuildable language inference evidence.

DROP INDEX IF EXISTS idx_vkpi_kol_pool_language_inferred;

ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS language_inferred_method;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS language_inferred_at;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS language_inferred_sample_n;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS language_inferred_source;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS language_inferred_confidence;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS language_inferred;

DELETE FROM schema_migrations
WHERE version_key='305_vkpi_kol_pool_language_inferred.sql';
