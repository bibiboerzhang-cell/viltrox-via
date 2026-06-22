-- 180_down — 回滚:移除 Agent 编排层留痕表。
BEGIN;
DROP TABLE IF EXISTS vkpi_agent_tool_run;
DROP TABLE IF EXISTS vkpi_agent_orchestration_plan;
COMMIT;
