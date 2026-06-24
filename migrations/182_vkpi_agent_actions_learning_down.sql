-- 182_down — 回滚学习闭环建表。
BEGIN;
DROP TABLE IF EXISTS vkpi_agent_outcome_evaluations;
DROP TABLE IF EXISTS vkpi_agent_actions;
COMMIT;
