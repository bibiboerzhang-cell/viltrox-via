-- 304: 综合上限降到 1000 美元每月 + 补种 12 个「有真实调用方、库里却没有 caps 行」的 scope。
--
-- 用户裁决(2026-08-25):综合上限 1000 美元每月;子闸高于总闸不合逻辑,一并压到总闸之下。
--
-- ── 第一段:把高于新总闸的 cap 压下来 ──────────────────────────────────────────
-- 隔离克隆取证(runtime/prod-sync/latest/prod-db.dump,采样时点 2026-08-25T20:39Z):
--   全表 42 行里,cap_usd 大于等于 1000 的**恰好三行**,逐行处置如下。
--
--   1) monthly_total          3000.00 月窗  已用 97.88  -> 1000.00
--      近 30 天全域实付 109.47 美元(台账 5936 次调用),1000 美元是 9.1 倍余量,不会误伤。
--   2) provider:gemini        1500.00 月窗  已用 28.83  -> 400.00
--      用户点名:子闸 1500 高于总闸 1000,逻辑上不成立。近 30 天 gemini 实付 30.03 美元
--      (1897 次调用),400 美元是 13.3 倍余量;gemini 是视频深析的主力 provider,
--      留足回填波动空间,所以不压到贴近实付的两位数。
--   3) cron:vkpi_analysis_worker 1500.00 **日窗** 已用 0.12 -> 50.00
--      用户清单里没有这一条,是本次核对新捞出来的同类问题:scope 带 cron: 前缀
--      = 日窗(见 backend/app/domains/costs/budget_windows.py budget_window_kind),
--      于是这行的真实语义是「1500 美元每天」—— 比新总闸还高,且是月闸的 45 倍。
--      同类深析口径 audit_video_analysis 近 30 天 18.55 美元,约合 0.62 美元每天;
--      50 美元每天已是 80 倍余量,既堵住失控又绝不卡正常回填。
--
--   刻意不动的三行(已经在新总闸之下,最小干预):
--     provider:apify  300.00 月窗 已用 48.39 —— 近 30 天 53.43 美元、占全域花费 49 percent,
--       是真正的大头;300 仍有 5.6 倍余量,压它才是误伤风险。
--     provider:claude 500.00 月窗 已用 19.62 —— 500 小于 1000,不构成「子闸高于总闸」。
--     provider:openai 500.00 月窗 已用  1.00 —— 同上。
--
--   幂等 + 单调收紧:WHERE cap_usd > 目标值。重跑一次不再命中(幂等);运维事后把额度调得
--   更低也不会被本迁移抬回去(只收紧、永不放宽)。刻意**不**按「等于旧值」做守卫 ——
--   那样一旦现网额度漂移过,用户的裁令就会静默落空。
--   只改 cap_usd:current_spend 与 reset_at 一律不碰,窗口锚点与已花额度保持连续。
--
-- ── 第二段:补种 12 个无 caps 行的 scope ──────────────────────────────────────
-- 核对方法(纯 SELECT,零写):把审计给的 15 个 scope 逐个 LEFT JOIN 现网 caps 表,
--   15 个**全部**确认没有 caps 行;再逐个回代码确认 scope 字面量的真实拼法
--   (backend/app/platform/llm_gateway.py _cost_scope_for_purpose:传了 cost_tag 就用
--    cost_tag 原文,只传 purpose 则拼成 cron: 前缀)。本迁移只种字面量已核实的那些。
--
-- 重要更正 —— 补种的理由不是「解开 100 percent 降级」:
--   303 那一批的病根是 llm_production.generate_json 的 require_configured_budget 默认 True,
--   无 caps 行 = 硬拦。本批 12 个逐个回溯调用链后确认**都不走那条路**:
--     generate_text            强制 require_configured_budget=False(llm_production.py:87)
--     generate_google_content  budget_preflight require_configured=False(llm_production_google.py:128)
--     generate_anthropic_messages 同上(llm_production_anthropic.py:88)
--     llm_gateway.invoke       require_configured_budget 默认 False
--     budget_guard.check_budget require_configured 默认 False(budget_guard.py:180)
--   所以这 12 个 scope 现在**没有被拦死**,它们是「花了钱但记不进分项、也没有任何上限」。
--   台账取证:这 15 个 scope 全时段累计 0 行 —— 不是因为被拦,是因为确实还没跑起来。
--   补种的真实收益有两条:一是 budget_guard.record_cost 只对已存在的 caps 行做 UPDATE,
--   没有种子行的花费在成本面板上完全看不见;二是总闸压到 1000 之后,per-scope 上限
--   是第二道防线,不能留 12 个「无上限」的口子。
--
--   反向风险必须写明:种下一行 caps 就把该 scope 从「不设防」变成「受闸管」。
--   额度给低了会**制造**出原本不存在的拦截。因此本批额度一律给到实测量级的十倍以上,
--   宁可宽一档,由总闸 1000 兜底。
--
-- 窗口语义与 296 / 303 保持同一口径:cron: 前缀 = 日窗;无前缀 / projects: 前缀 = 月窗。
--   reset_at 一律留 NULL:日窗首次读取即惰性清零并落锚点,月窗首次读取只补锚点不清零。
-- current_spend 一律从 0 起算:vkpi_ai_cost_ledger 是真账本,绝不改写、也不回填。
-- ON CONFLICT (scope) DO NOTHING:已存在的同名行(含运维手建 / 手调过额度的)一行不动。
--
-- ── 刻意不补的三个 scope(有反向证据,不瞎补)──────────────────────────────────
--   1) cron:dealer_web_verify —— 迁移 296 的评审**显式排除**过它,且
--      tests/test_migration_296_budget_scope_registry.py:62 把「不得出现」钉成了断言。
--      调用方 scripts/ops/dealer_web_verify.py 是运维手跑的离线脚本,不是运行时路径。
--      本迁移不单方面推翻上一轮评审结论,留给用户裁决。
--   2) vkpi_product_persona —— 同属离线批跑(backend/scripts_local/build_product_personas.py),
--      且该脚本自己的提示语写明「预算走 1000 美元每月闸」,即作者本就把 monthly_total
--      当作它的唯一护栏。种一个偏低的月闸反而会把运维的整批重建打断在半路。
--   3) vkpi_kol_memory_summary —— backend/app/domains/kol/memory.py:36-38 用代码注释
--      立了明确契约:「预算放开 = record-only,绝不硬拦」「无 caps 行 = record-only」。
--      种下 caps 行就等于把这条契约反过来,属于行为变更而非补漏,同样留给用户裁决。
--
-- 红线:本迁移零表结构变更;零触 viltrox_fit_score / rule_v0;不放宽任何质量口径
--   (新鲜度天数 / 器材证据 / 粉丝下限 / required_terms 一个字节没碰);
--   本文件不含 ASCII 问号与百分号字面量(compat 占位符与 LIKE 陷阱)。
-- The migration runner owns the surrounding transaction and advisory lock. Do not add BEGIN/COMMIT here.

UPDATE vkpi_provider_budget_caps
   SET cap_usd = 1000.00
 WHERE scope = 'monthly_total'
   AND cap_usd > 1000.00;

UPDATE vkpi_provider_budget_caps
   SET cap_usd = 400.00
 WHERE scope = 'provider:gemini'
   AND cap_usd > 400.00;

UPDATE vkpi_provider_budget_caps
   SET cap_usd = 50.00
 WHERE scope = 'cron:vkpi_analysis_worker'
   AND cap_usd > 50.00;

INSERT INTO vkpi_provider_budget_caps
    (scope, cap_usd, current_spend, warning_at, hard_stop_at, reset_at, fallback_action, metadata_json)
VALUES
    -- 日窗(cron: 前缀)。基线 2.00 美元每天,与 296 / 303 同档。
    ('cron:kol_account_dossier', 2.00, 0, 0.80, 1.00, NULL, 'skip_llm_keep_last',
     '{"seeded_by":"migration_304","window":"daily","gate":"record_only_no_hard_block","caller":"backend/app/services/kol/account_dossier.py","scope_form":"purpose_only"}'),
    ('cron:vkpi_discovery_localize', 2.00, 0, 0.80, 1.00, NULL, 'keep_original_text',
     '{"seeded_by":"migration_304","window":"daily","gate":"record_only_no_hard_block","caller":"backend/app/domains/kol/profile_discovery_localize.py","scope_form":"purpose_only"}'),
    ('cron:audience_avatar', 2.00, 0, 0.80, 1.00, NULL, 'fallback_to_rule_v0',
     '{"seeded_by":"migration_304","window":"daily","gate":"record_only_no_hard_block","caller":"backend/app/domains/kol/audience_avatar_llm.py","scope_form":"purpose_only"}'),
    -- 深析口径吃量最大的一条:同类 audit_video_analysis 近 30 天 18.55 美元(约 0.62 每天),
    -- 给 5.00 每天 = 8 倍余量,其余同批给 2.00 已是数十倍余量。
    ('cron:audit_deep_score', 5.00, 0, 0.80, 1.00, NULL, 'fallback_to_rule_v0',
     '{"seeded_by":"migration_304","window":"daily","gate":"record_only_no_hard_block","caller":"backend/app/services/ai/analyzers/claude_text.py","scope_form":"explicit_cost_tag"}'),
    ('cron:audit_vision_fallback', 2.00, 0, 0.80, 1.00, NULL, 'fallback_to_rule_v0',
     '{"seeded_by":"migration_304","window":"daily","gate":"record_only_no_hard_block","caller":"backend/app/services/ai/analyzers/claude_vision_images.py","scope_form":"explicit_cost_tag"}'),
    ('cron:intelligence_market', 2.00, 0, 0.80, 1.00, NULL, 'fallback_to_evidence_only',
     '{"seeded_by":"migration_304","window":"daily","gate":"record_only_no_hard_block","caller":"backend/app/services/intelligence/market.py","scope_form":"purpose_only"}'),
    ('cron:intelligence_brand', 2.00, 0, 0.80, 1.00, NULL, 'fallback_to_evidence_only',
     '{"seeded_by":"migration_304","window":"daily","gate":"record_only_no_hard_block","caller":"backend/app/services/intelligence/brand.py","scope_form":"purpose_only"}'),
    ('cron:lens_compare', 2.00, 0, 0.80, 1.00, NULL, 'skip_llm_keep_last',
     '{"seeded_by":"migration_304","window":"daily","gate":"record_only_no_hard_block","caller":"backend/app/services/intelligence/lens_compare.py","scope_form":"purpose_only"}'),
    ('cron:lens_monitor', 2.00, 0, 0.80, 1.00, NULL, 'skip_llm_keep_last',
     '{"seeded_by":"migration_304","window":"daily","gate":"record_only_no_hard_block","caller":"backend/app/services/intelligence/lens_monitor.py","scope_form":"purpose_only"}'),
    ('cron:brand_analysis', 2.00, 0, 0.80, 1.00, NULL, 'fallback_to_evidence_only',
     '{"seeded_by":"migration_304","window":"daily","gate":"record_only_no_hard_block","caller":"backend/app/api/routers/brand_analysis.py","scope_form":"purpose_only"}'),
    -- 月窗(无 cron: 前缀 / projects: 前缀)。基线 10.00 美元每月,与 296 / 303 同档。
    ('projects:invoice_extract', 10.00, 0, 0.80, 1.00, NULL, 'block_invoice_extract',
     '{"seeded_by":"migration_304","window":"monthly","gate":"record_only_no_hard_block","caller":"backend/app/domains/projects/contract_assist.py","scope_form":"explicit_cost_tag"}'),
    -- 与已在册的 agent_skill 同一编排族,fallback 文案沿用它的既有口径。
    ('marketing_brain_skill', 10.00, 0, 0.80, 1.00, NULL, 'rule_mode_dry_run',
     '{"seeded_by":"migration_304","window":"monthly","gate":"record_only_no_hard_block","caller":"backend/app/domains/marketing_brain/skill_orchestrator.py","scope_form":"explicit_budget_scope"}')
ON CONFLICT (scope) DO NOTHING;
