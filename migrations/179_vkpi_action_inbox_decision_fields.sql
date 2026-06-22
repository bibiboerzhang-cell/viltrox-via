-- 179_vkpi_action_inbox_decision_fields.sql — 路线0:把 Action Inbox「做实」。
-- 给 vkpi_action_inbox 补「决策四件套 + 验收回执」:每条建议带 预计收益/风险等级/证据引用,
-- 执行后写标准化验收回执;approval_reason 记人审批准理由。additive、幂等、零触 viltrox_fit_score。
-- 与既有 reason(为什么)/estimated_cost_cents(预计成本)/priority 互补 → 建议→批准→执行→验收 四段式。
BEGIN;
ALTER TABLE vkpi_action_inbox
  ADD COLUMN IF NOT EXISTS expected_gain TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS risk_level TEXT NOT NULL DEFAULT 'low',
  ADD COLUMN IF NOT EXISTS evidence_refs_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS result_checklist_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS approval_reason TEXT NOT NULL DEFAULT '';

-- risk_level 只允许 low|medium|high(NOT VALID 避免对历史行做全表校验,新写值受约束)。
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.constraint_column_usage
    WHERE table_name = 'vkpi_action_inbox' AND constraint_name = 'chk_vkpi_action_inbox_risk_level'
  ) THEN
    ALTER TABLE vkpi_action_inbox
      ADD CONSTRAINT chk_vkpi_action_inbox_risk_level
      CHECK (risk_level IN ('low','medium','high')) NOT VALID;
  END IF;
END $$;

COMMENT ON COLUMN vkpi_action_inbox.expected_gain IS '路线0:预计收益(人话);与 reason/cost 同列展示,组成决策四件套。';
COMMENT ON COLUMN vkpi_action_inbox.result_checklist_json IS '路线0:执行后标准化验收回执(创建几个 job/写几行/失败原因/是否花钱),executor 写。';
COMMIT;
