-- 129 回滚:删除履约内容帖子候选表及其索引。
-- 本表为隔离表,vkpi_projects / vkpi_project_kol_assignments / vkpi_cost_ledger 不受影响。
DROP INDEX IF EXISTS uq_vkpi_pcp_project_url;
DROP INDEX IF EXISTS idx_vkpi_pcp_assignment;
DROP INDEX IF EXISTS idx_vkpi_pcp_project_status;
DROP TABLE IF EXISTS vkpi_project_content_posts;
