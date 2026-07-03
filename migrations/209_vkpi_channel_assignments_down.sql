-- 209 回滚:删官方账号分配表(additive 表,回滚只丢分配关系,不伤账号与指标数据)。
BEGIN;
DROP TABLE IF EXISTS vkpi_channel_assignments;
COMMIT;
