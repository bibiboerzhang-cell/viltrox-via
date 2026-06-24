-- 181_vkpi_action_inbox_verification.sql — S1:Action Inbox 完整化(分析报告 W1 步骤1 点名)。
-- 补「执行前验证计划 + 影响表」两列:verification_plan(批准前看"会这样验证成功")+ affected_tables
-- (这条 action 执行会动哪些表 → executor 据此取执行前后真 COUNT 做 before/after)。
-- 与 179 的决策四件套 + result_checklist 互补,凑齐 建议→批准→执行→验收 全链可解释。
-- additive、幂等、零触 viltrox_fit_score。
BEGIN;
ALTER TABLE vkpi_action_inbox
  ADD COLUMN IF NOT EXISTS verification_plan_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS affected_tables_json   JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN vkpi_action_inbox.verification_plan_json IS
  'S1:执行前验证计划(人话清单,"怎么算成功");批准前可见,组成可解释闭环。';
COMMENT ON COLUMN vkpi_action_inbox.affected_tables_json IS
  'S1:这条 action 执行会写哪些表;executor 据此取执行前后真 COUNT 做 before/after delta。';
COMMIT;
