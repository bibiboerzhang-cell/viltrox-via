-- 131 回滚:删除真·项目共享成员表及其索引。
-- 本表为隔离表,vkpi_projects / staff 不受影响(回滚只去掉「加宽」,既有 own-only 不动)。
DROP INDEX IF EXISTS idx_vkpi_project_members_project;
DROP INDEX IF EXISTS idx_vkpi_project_members_staff;
DROP TABLE IF EXISTS vkpi_project_members;
