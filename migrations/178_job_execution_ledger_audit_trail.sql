-- 178_job_execution_ledger_audit_trail.sql — R21 留痕补全:job_execution_ledger 补审计列。
-- 加「谁触发(triggered_by_staff_id)/任务链上下文(task_chain_json:job_type/reason/category)/
-- 实耗成本(cost_cents,终态记)」三列 + triggered_by 索引。additive、幂等、零触评分域。
-- 与既有 estimated_cost(迁移 056)互补:estimated_cost 是预估(不变),cost_cents 是实耗。
BEGIN;
-- 与本表既有 payload_json/extra_json 同款用 TEXT 存 JSON 串(本表 json 列均为 TEXT,
-- queue.py 以 json.dumps 串 + '?' 占位写入,不做 ::jsonb 转换)。
ALTER TABLE job_execution_ledger
  ADD COLUMN IF NOT EXISTS triggered_by_staff_id BIGINT,
  ADD COLUMN IF NOT EXISTS task_chain_json TEXT NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS cost_cents BIGINT NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_job_execution_ledger_triggered_by
  ON job_execution_ledger(triggered_by_staff_id, created_at DESC);

COMMENT ON COLUMN job_execution_ledger.triggered_by_staff_id IS
  'R21:谁触发本次运行(取自 payload.staff_id/user_id);append-only 审计,落定不改。';
COMMIT;
