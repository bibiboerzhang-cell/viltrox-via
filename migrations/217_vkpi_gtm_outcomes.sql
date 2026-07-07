-- 217_vkpi_gtm_outcomes.sql — GTM 级结果总账(闭环波 L2,规格第二章数据模型照单全收)。
-- 背景:闭环 = 预判、行动、执行、记录、对答案、调整;四本账里 Outcome Ledger 是主战场
--   (recommendation_outcomes 778 行 KOL-only、裁决 2、finalized 0)。本表把结果账本升级到
--   「产品x市场x渠道x动作x结果」的 GTM 总账;KOL 只是 channel='creator' 的一种,
--   recommendation_outcomes 保留为 KOL 专用明细账,本表经桥接列 kol_pool_id/action_inbox_id 对账。
-- 写入方:
--   1. 人工裁决(market_brain/verdict_flow.record_verdict,经 POST 端点)——唯一能写
--      decision/lesson/decided_at/decided_by 的路径;decided 即 finalized,绝无自动裁决。
--   2. 三窗自动回填 job(L3 refresh_gtm_windows)只填 window_7d/14d/28d 与 actual_result,
--      能自动的自动填,填不了的诚实留空给人工裁决。
-- decision 取值:open(未裁决) / validated(验证成立) / failed(证伪) / partial(部分成立) /
--   retry(重试) / escalate(加码) / retreat(撤退)。open 只是初始态,POST 裁决只收后六种。
-- action_type 十一种口径(动作类型,建 bet 时定):
--   kol_outreach(达人外联) / dealer_push(经销商推动) / official_post(官号发布) /
--   indie_site_update(独立站更新) / review_collection(评测征集) / community_test(社区实测) /
--   paid_boost(付费放大) / content_retry(内容重试) / landing_page_fix(落地页修复) /
--   price_message_test(价格信息试探) / competitor_response(竞品应对)。
--   口径由应用层白名单约束,库层不加 CHECK(动作类型允许随规格演进,避免 apply 期硬卡)。
-- 三窗纪律:window_7d 执行效率 / window_14d 内容表现 / window_28d 商业结果 —— 三窗并行绝不单窗。
-- additive、幂等(IF NOT EXISTS);注释零 ASCII 问号、零百分号(避 compat 占位符炸 apply 的陷阱)。
-- 红线:纯结果账本,绝不触 viltrox_fit_score、不碰 rule_v0;权重回流只经
--   next_weight_change 结构化记录,真生效走既有 recommendation_feedback 链且带样本闸。
-- 回滚见 217_vkpi_gtm_outcomes_down.sql(DROP TABLE IF EXISTS)。
--
-- 字段:
--   id                 BIGSERIAL 主键        —— 结果行自增 id。
--   gtm_plan_id        TEXT 缺省 ''          —— 归属 GTM Plan(preview/materialize 的计划锚,文本键)。
--   product_sku        TEXT 缺省 ''          —— 产品 SKU。
--   market             TEXT 缺省 ''          —— 目标市场(如 US / JP;空 = 未指定)。
--   segment            TEXT 缺省 ''          —— 人群或细分(persona 口径)。
--   channel            TEXT 缺省 ''          —— 渠道(creator / dealer / official / indie_site / paid)。
--   action_type        TEXT 缺省 ''          —— 动作类型(上述十一种,应用层白名单)。
--   content_angle      TEXT 缺省 ''          —— 内容角度(awe / identity / before_after 等)。
--   expected_result    JSONB                 —— 预期结果(bet 的 expected 段:量化指标+目标值)。
--   actual_result      JSONB                 —— 实际结果汇总(回填 job 或人工录入)。
--   window_7d          JSONB                 —— 7 天执行效率窗(联系/回复/寄样/发布)。
--   window_14d         JSONB                 —— 14 天内容表现窗(播放/评论/保存/点击)。
--   window_28d         JSONB                 —— 28 天商业结果窗(订单/GMV/ROI,本地诚实 pending)。
--   decision           TEXT 缺省 'open'      —— 裁决(open 加后六种,CHECK 兜底)。
--   lesson             TEXT 缺省 ''          —— 一句话教训(裁决时人工写)。
--   next_weight_change JSONB                 —— 结构化权重调整条目(L4 接 recommendation_feedback 生效链)。
--   action_inbox_id    BIGINT                —— 桥接 vkpi_action_inbox.id(bet 所在行,不做 FK 免耦合)。
--   kol_pool_id        BIGINT 可空           —— 桥接 vkpi_kol_pool.id(仅 creator 渠道有值)。
--   created_at         TIMESTAMPTZ 缺省 NOW  —— 结果行建立时刻。
--   decided_at         TIMESTAMPTZ           —— 裁决时刻(非空即 finalized)。
--   decided_by         BIGINT                —— 裁决人 staff id(人工裁决唯一路径,诚实留痕)。
BEGIN;
CREATE TABLE IF NOT EXISTS vkpi_gtm_outcomes (
    id                 BIGSERIAL PRIMARY KEY,
    gtm_plan_id        TEXT NOT NULL DEFAULT '',
    product_sku        TEXT NOT NULL DEFAULT '',
    market             TEXT NOT NULL DEFAULT '',
    segment            TEXT NOT NULL DEFAULT '',
    channel            TEXT NOT NULL DEFAULT '',
    action_type        TEXT NOT NULL DEFAULT '',
    content_angle      TEXT NOT NULL DEFAULT '',
    expected_result    JSONB,
    actual_result      JSONB,
    window_7d          JSONB,
    window_14d         JSONB,
    window_28d         JSONB,
    decision           TEXT NOT NULL DEFAULT 'open',
    lesson             TEXT NOT NULL DEFAULT '',
    next_weight_change JSONB,
    action_inbox_id    BIGINT,
    kol_pool_id        BIGINT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at         TIMESTAMPTZ,
    decided_by         BIGINT,
    CONSTRAINT chk_vkpi_gtm_outcomes_decision
        CHECK (decision IN ('open', 'validated', 'failed', 'partial', 'retry', 'escalate', 'retreat'))
);

CREATE INDEX IF NOT EXISTS idx_vkpi_gtm_outcomes_decision_created
    ON vkpi_gtm_outcomes (decision, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_gtm_outcomes_inbox
    ON vkpi_gtm_outcomes (action_inbox_id);

CREATE INDEX IF NOT EXISTS idx_vkpi_gtm_outcomes_plan
    ON vkpi_gtm_outcomes (gtm_plan_id);

CREATE INDEX IF NOT EXISTS idx_vkpi_gtm_outcomes_sku_market
    ON vkpi_gtm_outcomes (product_sku, market);

CREATE INDEX IF NOT EXISTS idx_vkpi_gtm_outcomes_kol
    ON vkpi_gtm_outcomes (kol_pool_id);

COMMENT ON TABLE vkpi_gtm_outcomes IS
  '闭环波 L2 GTM 级结果总账: 产品x市场x渠道x动作x结果; 三窗(7d/14d/28d)并行对答案; decision 只由人工裁决端点写入, decided 即 finalized; KOL 明细留 recommendation_outcomes, 本表经 kol_pool_id/action_inbox_id 桥接; 零触 viltrox_fit_score。';
COMMIT;
