-- 166_vkpi_llm_batches_down.sql — 回滚 Batch API 台账
BEGIN;
DROP TABLE IF EXISTS vkpi_llm_batches;
COMMIT;
