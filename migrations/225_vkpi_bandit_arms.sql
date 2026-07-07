-- 225_vkpi_bandit_arms.sql — C4 W10 放权能力:bandit-lite arm 权重账(deploy dark,默认不启用)。
-- 背景:GTM 探索/利用的最小可证据化实验框架。arm = 一组可对比的投放组合
--   (product_sku x market x channel x content_angle x creator_or_dealer_type),
--   每条 arm 累积归一化奖励的在线均值(mean_reward)与样本数(n),供后续按 UCB 口径
--   给出「下一步探索谁」的纯建议。写入方仅 market_brain/bandit.record_arm_reward
--   (增量统计),LLM 绝不直接改权重,选臂只出建议绝不自动执行。
-- 迁移含两件:
--   1. 建 vkpi_bandit_arms 表(additive、幂等 IF NOT EXISTS);
--   2. 种 scheduler_tasks 一行 vkpi_bandit_weight_refresh —— 默认 enabled=FALSE(OFF),
--      放权闸 W10 开闸后才由运营在 Ops 页显式启用,本迁移落地后绝不自动运行。
-- 幂等:表建用 IF NOT EXISTS;种子用 ON CONFLICT(task_key) DO NOTHING(沿用 218/224 同款)。
-- 红线:纯统计账本,零触 viltrox_fit_score、零碰 rule_v0;注释零 ASCII 疑问号、零 percent 字面量
--   (避 compat 占位符炸 apply 的陷阱)。回滚见 225_vkpi_bandit_arms_down.sql。
--
-- 字段:
--   id                      BIGSERIAL 主键        —— arm 行自增 id。
--   arm_key                 TEXT UNIQUE           —— 稳定组合键(维度小写、'|' 分隔),幂等锚。
--   product_sku             TEXT 缺省 ''          —— 产品 SKU。
--   market                  TEXT 缺省 ''          —— 目标市场(如 US / JP)。
--   channel                 TEXT 缺省 ''          —— 渠道(creator / dealer / official / indie_site / paid)。
--   content_angle           TEXT 缺省 ''          —— 内容角度(awe / identity / before_after 等)。
--   creator_or_dealer_type  TEXT 缺省 ''          —— 达人或经销商类型(投放对象画像)。
--   n                       INT 缺省 0            —— 样本数(累计观测次数)。
--   mean_reward             DOUBLE 缺省 0         —— 在线均值奖励(0-1 归一)。
--   last_reward             DOUBLE 缺省 0         —— 最近一次归一化奖励(0-1)。
--   created_at              TIMESTAMPTZ 缺省 NOW  —— arm 行建立时刻。
--   updated_at              TIMESTAMPTZ 缺省 NOW  —— 最近一次落账时刻。
BEGIN;
CREATE TABLE IF NOT EXISTS vkpi_bandit_arms (
    id                     BIGSERIAL PRIMARY KEY,
    arm_key                TEXT             NOT NULL UNIQUE,
    product_sku            TEXT             NOT NULL DEFAULT '',
    market                 TEXT             NOT NULL DEFAULT '',
    channel                TEXT             NOT NULL DEFAULT '',
    content_angle          TEXT             NOT NULL DEFAULT '',
    creator_or_dealer_type TEXT             NOT NULL DEFAULT '',
    n                      INT              NOT NULL DEFAULT 0,
    mean_reward            DOUBLE PRECISION NOT NULL DEFAULT 0,
    last_reward            DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at             TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vkpi_bandit_arms_mean_reward
    ON vkpi_bandit_arms (mean_reward DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_bandit_arms_sku_market
    ON vkpi_bandit_arms (product_sku, market);

COMMENT ON TABLE vkpi_bandit_arms IS
  'C4 W10 bandit-lite arm 权重账: sku x market x channel x content_angle x creator_or_dealer_type 组合累积在线均值奖励; 仅统计入账不做自动执行; 零触 viltrox_fit_score。';

-- 种子:放权刷新闸门,默认 enabled=FALSE(OFF),W10 开闸后运营显式启用。
INSERT INTO scheduler_tasks (task_key, label, risk_level) VALUES
    ('vkpi_bandit_weight_refresh', 'Bandit arm 权重刷新(放权闸,默认关,人工裁决后才更新)', 'low')
ON CONFLICT (task_key) DO NOTHING;
COMMIT;
