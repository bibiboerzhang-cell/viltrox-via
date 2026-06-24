-- 181_down — 回滚 S1 验证计划 + 影响表列。
BEGIN;
ALTER TABLE vkpi_action_inbox
  DROP COLUMN IF EXISTS verification_plan_json,
  DROP COLUMN IF EXISTS affected_tables_json;
COMMIT;
