-- 133 回滚:去掉公司公共标记列(只去掉「加宽」,既有 own/restricted/共享可见性不动)。
ALTER TABLE vkpi_events DROP COLUMN IF EXISTS is_public;
ALTER TABLE vkpi_projects DROP COLUMN IF EXISTS is_public;
