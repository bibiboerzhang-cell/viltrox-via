-- 132 回滚:删除真·活动共享成员表及其索引。
-- 本表为隔离表,vkpi_events / staff 不受影响(回滚只去掉「加宽」,既有 owner/team_ids 不动)。
DROP INDEX IF EXISTS idx_vkpi_event_members_event;
DROP INDEX IF EXISTS idx_vkpi_event_members_staff;
DROP TABLE IF EXISTS vkpi_event_members;
