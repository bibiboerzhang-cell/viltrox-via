-- 193 down — 移除 Durable Workflow 引擎表。
BEGIN;
DROP TABLE IF EXISTS vkpi_workflow_checkpoints;
DROP TABLE IF EXISTS vkpi_workflow_steps;
DROP TABLE IF EXISTS vkpi_workflow_runs;
COMMIT;
