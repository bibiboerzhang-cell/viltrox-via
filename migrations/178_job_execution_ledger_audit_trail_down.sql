-- 178_down — 回滚:移除 R21 审计列 + 索引。
BEGIN;
DROP INDEX IF EXISTS idx_job_execution_ledger_triggered_by;
ALTER TABLE job_execution_ledger
  DROP COLUMN IF EXISTS triggered_by_staff_id,
  DROP COLUMN IF EXISTS task_chain_json,
  DROP COLUMN IF EXISTS cost_cents;
COMMIT;
