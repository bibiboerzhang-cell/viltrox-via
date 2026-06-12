-- 109 down
DROP INDEX IF EXISTS idx_vkpi_kol_pool_duplicate_of;
ALTER TABLE vkpi_kol_pool DROP COLUMN IF EXISTS duplicate_of_id;
