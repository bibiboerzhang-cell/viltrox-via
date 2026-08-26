-- 回滚 304:把三条被压低的 cap 抬回旧值,并删掉本迁移种下的 12 条 scope 行。
-- 台账 vkpi_ai_cost_ledger 一行不动;current_spend 与 reset_at 一律不碰。
--
-- 抬回时用「等于本迁移写下的值」做守卫(1000 / 400 / 50):
--   运维在成本面板事后又调过额度的行,值已经不等于播种值,于是原样保留 —— 回滚不覆盖
--   比自己更新的人为决定。代价是这类行会停在运维设定的额度上,而不是旧的 3000 / 1500;
--   这正是想要的:回滚撤销的是**本迁移的那一笔**,不是它之后发生的一切。
--
-- 删除行用 strpos 守卫出身(禁 LIKE 与百分号字面量)。UP 是 ON CONFLICT DO NOTHING,
--   所以带 migration_304 标记的行当且仅当是本迁移新插的;更早迁移种下的、或运维手建的
--   同名 scope 行标记压根写不上去,因而原样保留。
--   已知不保证的一点(与 303 的 DOWN 同源,不再重复把话说大):运维只改过 cap_usd 的行,
--   metadata 会被 budget_guard.update_budget 原样写回(budget_guard.py:315 与 :335),
--   标记因此存活 -> 这类行照删。删干净、可重放,好过留半截无处可查的残留。
--
-- 注意:回滚后这 12 个 scope 重新变成「无 caps 行」。因为它们本就不走
--   require_configured=True 的路径,所以**不会**因此被拦死,只是重新失去分项记账与上限
--   —— 回到 304 之前的状态,不是新故障。

UPDATE vkpi_provider_budget_caps
   SET cap_usd = 3000.00
 WHERE scope = 'monthly_total'
   AND cap_usd = 1000.00;

UPDATE vkpi_provider_budget_caps
   SET cap_usd = 1500.00
 WHERE scope = 'provider:gemini'
   AND cap_usd = 400.00;

UPDATE vkpi_provider_budget_caps
   SET cap_usd = 1500.00
 WHERE scope = 'cron:vkpi_analysis_worker'
   AND cap_usd = 50.00;

DELETE FROM vkpi_provider_budget_caps
WHERE scope IN (
        'cron:kol_account_dossier',
        'cron:vkpi_discovery_localize',
        'cron:audience_avatar',
        'cron:audit_deep_score',
        'cron:audit_vision_fallback',
        'cron:intelligence_market',
        'cron:intelligence_brand',
        'cron:lens_compare',
        'cron:lens_monitor',
        'cron:brand_analysis',
        'projects:invoice_extract',
        'marketing_brain_skill'
      )
  AND strpos(metadata_json, 'migration_304') > 0;

DELETE FROM schema_migrations
WHERE version_key='304_vkpi_budget_ceiling_1000.sql';
