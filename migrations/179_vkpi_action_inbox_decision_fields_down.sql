-- 179_down — 回滚:移除路线0 决策四件套 + 验收回执列与约束。
BEGIN;
ALTER TABLE vkpi_action_inbox DROP CONSTRAINT IF EXISTS chk_vkpi_action_inbox_risk_level;
ALTER TABLE vkpi_action_inbox
  DROP COLUMN IF EXISTS expected_gain,
  DROP COLUMN IF EXISTS risk_level,
  DROP COLUMN IF EXISTS evidence_refs_json,
  DROP COLUMN IF EXISTS result_checklist_json,
  DROP COLUMN IF EXISTS approval_reason;
COMMIT;
