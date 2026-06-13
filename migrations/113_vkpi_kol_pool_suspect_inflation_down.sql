-- 113 down:回滚 P0-3 假粉/异常号独立列(不入 _POSTGRES_MIGRATION_SEQUENCE,需手动 apply)。
DROP INDEX IF EXISTS idx_vkpi_kol_pool_suspect_inflation;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS inflation_method;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS inflation_checked_at;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS inflation_signals_json;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS inflation_reason;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS suspect_inflation;
