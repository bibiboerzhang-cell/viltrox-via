-- 回滚 159:共享 KOL 池。
DROP INDEX IF EXISTS idx_vkpi_kol_pool_members_pool;
DROP INDEX IF EXISTS idx_vkpi_kol_pool_members_staff;
DROP TABLE IF EXISTS vkpi_kol_pool_members;
