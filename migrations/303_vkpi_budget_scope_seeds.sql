-- 303: LLM 预算 scope 补种 + scope 漂移对齐(收尾波 B 车道,审计 R8/R9)。
--
-- 病根:llm_production.generate_json 的 require_configured_budget 默认 True,网关
--   llm_gateway._budget_scopes_for_provider 会把 [monthly_total, single_call,
--   provider:xxx, cost_scope] 一并交给 check_budget_scopes(require_configured=True);
--   budget_guard.check_budget_scopes 对「没有 caps 行」的 scope 直接判 allowed=False。
--   于是 cost_scope 少一行种子 = 该功能 100 percent 降级 rule_v0,且永远不烧钱、也永远不出结果。
--   隔离克隆取证:vkpi_intelligent_ask 两次调用全是 all_providers_failed / provider=rule_v0
--   (最近一次 2026-08-10T17:30:49Z),ledger 累计 0 美元 —— 典型的「静默全灭」。
--   另一面是记账盲区:budget_guard.record_cost 只对已存在的 caps 行做 UPDATE current_spend,
--   没有种子行的 scope 花掉的钱只进 vkpi_ai_cost_ledger,成本面板上看不见分项。
--
-- 本迁移零表结构变更,纯 INSERT 幂等(ON CONFLICT (scope) DO NOTHING,scope 是主键)。
--   current_spend 一律从 0 起算:台账历史(vkpi_ai_cost_ledger)是真账本,绝不改写、也不回填。
--   已存在的同名行(含运维在面板手建、手调过额度的行)一行不动 —— DO NOTHING 不是 DO UPDATE。
--
-- 顺带补两个「只在现网手建、没有迁移出身」的 scope:audience_stats 与 vkpi_kol_content_fit。
--   隔离克隆取证:这两行 metadata_json 是空对象,全仓 migrations 里也搜不到它们的种子语句
--   —— 说明它们是运维从成本面板 update_budget 手敲出来的。库重建 / 新环境一律没有这两行,
--   而两个调用方都走 require_configured_budget=True,于是新环境开局就 100 percent 降级 rule_v0。
--   本迁移按现网同额度(10.00 美元每月)补进迁移链,现网那两行因 DO NOTHING 原样保留。
--
-- 窗口语义(与 backend/app/domains/costs/budget_windows.py 的分类严格对齐):
--   - cron: 前缀 = 日窗,reset_at 到期即清零并推进到下一个 UTC 零点 -> 本批统一 2.00 美元每天。
--     取证:deepsight 三脑并发单轮估价约 0.185 美元(claude-opus-5 0.0838 + gpt-5.5 0.0798
--     + gemini-2.5-pro 0.0208),2.00 美元每天约等于 10 轮每天,够用且不会一夜烧穿。
--   - 无前缀 / projects: 前缀 = 月窗(既非 cron: 也非 single_call 一律按月滚)-> 10.00 美元每月。
--   - reset_at 一律留 NULL:日窗首次读取即惰性清零并落锚点;月窗首次读取只补锚点不清零。
-- 额度是保守起步值,运维在成本面板 update_budget 可随时调额或清零,不必再发版。
--
-- scope 漂移三处(口径:只补真实调用方用的 scope,绝不改名历史台账行):
--   1) vkpi_pillar —— 调用方 backend/app/domains/content/pillars.py 传 cost_tag='vkpi_pillar',
--      库里种的却是 'cron:vkpi_pillar'(cap 15.00)。本迁移补 'vkpi_pillar';旧行原样保留,
--      它承载着历史台账与运维改过的额度,删了等于抹账。
--   2) cron:vkpi_weekly_summary —— backend/app/domains/reports/report_helpers.py 用
--      purpose='vkpi_weekly_summary',网关拼成 'cron:vkpi_weekly_summary',无种子。本迁移补上。
--      注:库里已有的 'cron:vkpi_weekly_report' 并非孤儿 —— 它对应第二个真调用方
--      backend/app/domains/reports/weekly_generator.py(purpose='vkpi_weekly_report'),两行并存才如实。
--   3) projects:contract_extract —— backend/app/services/ai/analyzers/claude_contract_extract.py
--      的 Anthropic PDF 路径按这个 tag 记账,而闸门 backend/app/domains/projects/contracts_extract.py
--      查的是已种的 'cron:vkpi_contract_extract'。两者都是真的,本迁移只补没种过的那个。
--
-- 红线:零触 viltrox_fit_score / rule_v0;本文件不含 ASCII 问号与百分号(compat 占位符陷阱)。
-- The migration runner owns the surrounding transaction and advisory lock. Do not add BEGIN/COMMIT here.

INSERT INTO vkpi_provider_budget_caps
    (scope, cap_usd, current_spend, warning_at, hard_stop_at, reset_at, fallback_action, metadata_json)
VALUES
    -- 日窗(cron: 前缀,2.00 美元每天)
    ('cron:marketing_advisor', 2.00, 0, 0.80, 1.00, NULL, 'fallback_to_rule_v0',
     '{"seeded_by":"migration_303","window":"daily","gate":"require_configured_budget","caller":"backend/app/domains/advisor/service.py"}'),
    ('cron:kol_outreach_pack', 2.00, 0, 0.80, 1.00, NULL, 'fallback_to_template',
     '{"seeded_by":"migration_303","window":"daily","gate":"require_configured_budget","caller":"backend/app/domains/kol/outreach_pack.py"}'),
    ('cron:vkpi_mention_sentiment', 2.00, 0, 0.80, 1.00, NULL, 'fallback_to_rule_v0',
     '{"seeded_by":"migration_303","window":"daily","gate":"require_configured_budget","caller":"backend/app/domains/market/mention_sentiment_annotate.py"}'),
    ('cron:vkpi_sentiment_annotate', 2.00, 0, 0.80, 1.00, NULL, 'fallback_to_rule_v0',
     '{"seeded_by":"migration_303","window":"daily","gate":"require_configured_budget","caller":"backend/app/domains/market/sentiment_annotate.py"}'),
    ('cron:deepsight_triad', 2.00, 0, 0.80, 1.00, NULL, 'fallback_to_rule_v0',
     '{"seeded_by":"migration_303","window":"daily","gate":"require_configured_budget","caller":"backend/app/services/deepsight/triad.py"}'),
    ('cron:vkpi_weekly_summary', 2.00, 0, 0.80, 1.00, NULL, 'fallback_to_rule',
     '{"seeded_by":"migration_303","window":"daily","gate":"ledger_visibility","drift":"actual_caller_scope","caller":"backend/app/domains/reports/report_helpers.py"}'),
    -- 月窗(无 cron: 前缀,10.00 美元每月)
    ('vkpi_intelligent_ask', 10.00, 0, 0.80, 1.00, NULL, 'fallback_to_search',
     '{"seeded_by":"migration_303","window":"monthly","gate":"require_configured_budget","caller":"backend/app/api/routers/vkpi_intelligent.py"}'),
    ('comment_reply_draft', 10.00, 0, 0.80, 1.00, NULL, 'fallback_to_rule_v0',
     '{"seeded_by":"migration_303","window":"monthly","gate":"require_configured_budget","caller":"backend/app/domains/comments/reply_queue.py"}'),
    ('vkpi_sentiment', 10.00, 0, 0.80, 1.00, NULL, 'fallback_to_rule_v0',
     '{"seeded_by":"migration_303","window":"monthly","gate":"require_configured_budget","caller":"backend/app/domains/comments/sentiment.py"}'),
    ('vkpi_pillar', 10.00, 0, 0.80, 1.00, NULL, 'fallback_to_rule',
     '{"seeded_by":"migration_303","window":"monthly","gate":"require_configured_budget","drift":"actual_caller_scope","legacy_row":"cron:vkpi_pillar","caller":"backend/app/domains/content/pillars.py"}'),
    ('vkpi_contract_polish', 10.00, 0, 0.80, 1.00, NULL, 'block_contract_polish',
     '{"seeded_by":"migration_303","window":"monthly","gate":"require_configured_budget","caller":"backend/app/domains/projects/contract_assist.py"}'),
    ('kol_outreach_draft', 10.00, 0, 0.80, 1.00, NULL, 'fallback_to_rule_v0',
     '{"seeded_by":"migration_303","window":"monthly","gate":"require_configured_budget","caller":"backend/app/domains/projects/outreach.py"}'),
    ('kol_content_scorer', 10.00, 0, 0.80, 1.00, NULL, 'fallback_to_rule_v0',
     '{"seeded_by":"migration_303","window":"monthly","gate":"require_configured_budget","caller":"backend/app/services/kol/content_scorer.py"}'),
    ('projects:contract_extract', 10.00, 0, 0.80, 1.00, NULL, 'block_contract_extract',
     '{"seeded_by":"migration_303","window":"monthly","gate":"ledger_visibility","drift":"actual_caller_scope","sibling_row":"cron:vkpi_contract_extract","caller":"backend/app/services/ai/analyzers/claude_contract_extract.py"}'),
    -- 补迁移出身(现网已有手建行,DO NOTHING 保原状,只为库重建 / 新环境不再开局全灭)
    ('audience_stats', 10.00, 0, 0.80, 1.00, NULL, 'fallback_to_rule_v0',
     '{"seeded_by":"migration_303","window":"monthly","gate":"require_configured_budget","provenance":"was_operator_created_only","caller":"backend/app/domains/kol/audience_stats_age.py"}'),
    ('vkpi_kol_content_fit', 10.00, 0, 0.80, 1.00, NULL, 'fallback_to_rule_v0',
     '{"seeded_by":"migration_303","window":"monthly","gate":"require_configured_budget","provenance":"was_operator_created_only","caller":"backend/app/domains/kol/content_fit_analysis.py"}')
ON CONFLICT (scope) DO NOTHING;
