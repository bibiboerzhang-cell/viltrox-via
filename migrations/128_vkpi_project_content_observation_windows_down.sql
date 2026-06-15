-- 128 回滚:删除履约观察窗口表及其索引。
-- 本表为隔离表,vkpi_projects / vkpi_project_kol_assignments / vkpi_cost_ledger / vkpi_shipments 不受影响。
DROP INDEX IF EXISTS idx_vkpi_pcow_status_ends;
DROP INDEX IF EXISTS idx_vkpi_pcow_project;
DROP TABLE IF EXISTS vkpi_project_content_observation_windows;
