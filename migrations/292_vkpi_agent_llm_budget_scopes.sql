-- 292: 异常哨兵 LLM 解释 + Skill 驾照真跑的预算 scope 种子(优化波 B · S 车道)。
-- 目的:两条新自动 LLM 路径都 require_configured=True,cap 行不存在即绝不烧钱;本迁移补种 cap 行,
--   运维只需在成本面板调额或清零。零表结构变更;纯 INSERT 幂等(ON CONFLICT DO NOTHING)。
--   agent_skill         : vkpi_skill_auto_orchestrate 在驾照 skill_orchestrate 达 L2 且放行时,
--                         为 creator_match 注入 gemini-3.6-flash 出 fit_reason;封顶 40 美元。
--   agent_alert_explain : anomaly 哨兵可选 LLM 解释(env VKPI_ALERT_EXPLAIN_LLM=1 才开,日限 30 条);封顶 5 美元。
-- 注意:这两个 scope 不带 cron: 或 provider: 前缀,budget_windows 不会自动按日或按月滚动清零,
--   current_spend 为累计值;按月清零需运维在面板 update_budget 置零(或由 O 车道扩 agent_ 前缀为月窗)。
-- 红线:零触 viltrox_fit_score / rule_v0;注释里禁用 ASCII 问号与百分号(compat 占位符陷阱)。

INSERT INTO vkpi_provider_budget_caps
    (scope, cap_usd, current_spend, warning_at, hard_stop_at, reset_at, fallback_action, metadata_json)
VALUES
    ('agent_skill', 40.00, 0, 0.80, 1.00, NULL, 'rule_mode_dry_run',
     '{"seeded_by":"migration_292","tier":"agent","package":"skill_auto_orchestrate","provider":"gemini","window":"manual_monthly"}'),
    ('agent_alert_explain', 5.00, 0, 0.80, 1.00, NULL, 'rule_explanation',
     '{"seeded_by":"migration_292","tier":"agent","package":"anomaly_sentinel_explain","provider":"gemini","window":"manual_monthly"}')
ON CONFLICT (scope) DO NOTHING;
