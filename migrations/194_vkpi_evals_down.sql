-- 194 down — 移除 Evals 表。
BEGIN;
DROP TABLE IF EXISTS vkpi_eval_results;
DROP TABLE IF EXISTS vkpi_eval_runs;
COMMIT;
