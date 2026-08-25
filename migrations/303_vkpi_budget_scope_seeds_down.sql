-- 回滚 303:只删本迁移种下的 16 条预算 scope 行。台账 vkpi_ai_cost_ledger 一行不动。
--
-- strpos 守卫到底保证什么(2026-08-25 对抗审查更正 —— 旧注释把话说大了):
--   保证的是**出身**。UP 是 ON CONFLICT (scope) DO NOTHING,所以 metadata_json 里带
--   'migration_303' 标记的行,当且仅当是本迁移新插的;更早迁移种下的、或运维在成本面板
--   手建的同名 scope 行,标记压根写不上去,因而原样保留。
--   **不保证**「运维在面板改过额度的行不被误删」—— 旧注释这半句与代码事实相反:
--   budget_guard.update_budget 只改 cap_usd 时,metadata 取的是旧行的
--   _load_json(old_data.get('metadata_json'))(backend/app/domains/costs/budget_guard.py:315),
--   再原样写回 metadata_json=excluded.metadata_json(同文件 :335),标记因此存活 -> 这类行照删。
--
-- 为什么不改成「同时比对 cap_usd 是否等于播种值」把守卫做真:那会让 DOWN 留下本迁移自己
--   插的行,而 UP 是 DO NOTHING,再跑一次 UP 也不会把它改回播种值 —— 回滚后的库与
--   「从未跑过 303」不再等价,下次排查凭空多一层暗坑。删干净、可重放,比留半截好查。
--   运维改过的额度在成本面板随时可重设,残留行造成的口径漂移却无处可查。
--
-- 注意:回滚后这些 scope 重新变成「无 caps 行」,require_configured_budget=True 的调用方
-- 会重新 100 percent 降级 rule_v0 —— 这是回到 303 之前的状态,不是新故障。

DELETE FROM vkpi_provider_budget_caps
WHERE scope IN (
        'cron:marketing_advisor',
        'cron:kol_outreach_pack',
        'cron:vkpi_mention_sentiment',
        'cron:vkpi_sentiment_annotate',
        'cron:deepsight_triad',
        'cron:vkpi_weekly_summary',
        'vkpi_intelligent_ask',
        'comment_reply_draft',
        'vkpi_sentiment',
        'vkpi_pillar',
        'vkpi_contract_polish',
        'kol_outreach_draft',
        'kol_content_scorer',
        'projects:contract_extract',
        'audience_stats',
        'vkpi_kol_content_fit'
      )
  AND strpos(metadata_json, 'migration_303') > 0;

DELETE FROM schema_migrations
WHERE version_key='303_vkpi_budget_scope_seeds.sql';
