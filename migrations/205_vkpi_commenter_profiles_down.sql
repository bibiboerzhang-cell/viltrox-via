-- 205 回滚:删评论者身份缓存表(纯缓存,可随时重建,无业务数据损失)。
BEGIN;
DROP TABLE IF EXISTS vkpi_commenter_profiles;
COMMIT;
