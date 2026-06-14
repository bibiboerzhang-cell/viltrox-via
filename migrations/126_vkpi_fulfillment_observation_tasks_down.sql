-- 126 回滚:删除履约观察任务表及其索引。
-- 本表为隔离任务表,vkpi_projects / vkpi_project_kol_assignments / vkpi_cost_ledger 不受影响。
DROP INDEX IF EXISTS uq_vkpi_fot_pending;
DROP INDEX IF EXISTS idx_vkpi_fot_status_created;
DROP INDEX IF EXISTS idx_vkpi_fot_project_status;
DROP TABLE IF EXISTS vkpi_fulfillment_observation_tasks;
