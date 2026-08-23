-- 回滚 289:去掉 vkpi_analysis_cache 的 prompt_version / model_family(纯派生列,可由 model 重算)。

DROP INDEX IF EXISTS idx_vkpi_analysis_cache_model_family;

ALTER TABLE vkpi_analysis_cache DROP COLUMN IF EXISTS model_family;
ALTER TABLE vkpi_analysis_cache DROP COLUMN IF EXISTS prompt_version;

DELETE FROM schema_migrations
WHERE version_key='289_vkpi_analysis_cache_model_family.sql';
