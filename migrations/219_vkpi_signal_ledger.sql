-- 219_vkpi_signal_ledger.sql — 外部/内部信号账本(全景规格 5.2 节 A 表照单落地)。
-- 背景:Reddit、YouTube 评论、Google Trends、Amazon/BH 评论、竞品官网、官号数据、
--   项目内部数据等所有市场信号先入本账本再消费;每条信号可追溯来源(source_type/
--   source_name/source_url)、可去重(dedupe_key)、可评鲜度与来源质量。
-- 消费方:market_brain/signal_ledger.summarize_for_preview 只读汇总(表缺失或零行时
--   诚实回 data_missing,绝不抛异常);后续 Opportunity Score 等算法按 sku+market 聚合。
-- organization_id 为商业化前多租户安全字段,当前 Viltrox 单租户缺省 'viltrox'。
-- additive、幂等(IF NOT EXISTS);注释零 ASCII 问号、零百分号(避 compat 占位符炸 apply 的陷阱)。
-- 红线:纯信号账本,绝不触 viltrox_fit_score、不碰 rule_v0 打分逻辑。
-- 回滚见 219_vkpi_signal_ledger_down.sql(DROP TABLE IF EXISTS)。
--
-- 字段:
--   id                   BIGSERIAL 主键           —— 信号行自增 id。
--   organization_id      TEXT 缺省 'viltrox'      —— 多租户安全字段。
--   source_type          TEXT NOT NULL            —— 来源类型(reddit / youtube_comments / google_trends / amazon_reviews / bh_reviews / competitor_site / official_channel / internal)。
--   source_name          TEXT NOT NULL            —— 来源名(子版块名 / 频道名 / 站点名等)。
--   source_url           TEXT                     —— 来源链接(可空,内部数据无链接)。
--   captured_at          TIMESTAMPTZ 缺省 NOW     —— 采集入账时刻。
--   observed_at          TIMESTAMPTZ              —— 信号在源头发生的时刻(可空)。
--   product_sku          TEXT                     —— 关联产品 SKU(可空 = 未归属)。
--   product_family       TEXT                     —— 产品族(如 AF 镜头 / 补光灯)。
--   market               TEXT                     —— 目标市场(如 US / JP)。
--   platform             TEXT                     —— 平台(youtube / reddit / amazon 等)。
--   topic                TEXT                     —— 主题标签(自由文本)。
--   entity_type          TEXT                     —— 关联实体类型(kol / dealer / product 等)。
--   entity_id            TEXT                     —— 关联实体 id(文本键,不做 FK 免耦合)。
--   signal_kind          TEXT NOT NULL            —— 信号种类(mention / review / trend / complaint 等)。
--   signal_text          TEXT NOT NULL            —— 信号正文摘要(展示与检索用)。
--   signal_value         DOUBLE PRECISION         —— 数值化信号(如趋势指数,可空)。
--   sentiment            DOUBLE PRECISION         —— 情感分(-1 到 1,可空 = 未评)。
--   momentum             DOUBLE PRECISION         —— 动量分(变化速率,可空)。
--   sample_size          INTEGER                  —— 样本量(该信号背后的条目数)。
--   freshness_score      DOUBLE PRECISION         —— 鲜度分(0 到 1)。
--   source_quality_score DOUBLE PRECISION         —— 来源质量分(0 到 1)。
--   dedupe_key           TEXT NOT NULL            —— 去重键(与 organization_id 联合唯一)。
--   raw_ref              JSONB 缺省 '{}'          —— 原始引用(指针不存敏感原文)。
--   normalized           JSONB 缺省 '{}'          —— 归一化结构(算法消费的标准形)。
--   created_at           TIMESTAMPTZ 缺省 NOW     —— 行建立时刻。
BEGIN;
CREATE TABLE IF NOT EXISTS vkpi_signal_ledger (
    id                   BIGSERIAL PRIMARY KEY,
    organization_id      TEXT NOT NULL DEFAULT 'viltrox',
    source_type          TEXT NOT NULL,
    source_name          TEXT NOT NULL,
    source_url           TEXT,
    captured_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    observed_at          TIMESTAMPTZ,
    product_sku          TEXT,
    product_family       TEXT,
    market               TEXT,
    platform             TEXT,
    topic                TEXT,
    entity_type          TEXT,
    entity_id            TEXT,
    signal_kind          TEXT NOT NULL,
    signal_text          TEXT NOT NULL,
    signal_value         DOUBLE PRECISION,
    sentiment            DOUBLE PRECISION,
    momentum             DOUBLE PRECISION,
    sample_size          INTEGER,
    freshness_score      DOUBLE PRECISION,
    source_quality_score DOUBLE PRECISION,
    dedupe_key           TEXT NOT NULL,
    raw_ref              JSONB NOT NULL DEFAULT '{}'::jsonb,
    normalized           JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (organization_id, dedupe_key)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_signal_ledger_sku_market
    ON vkpi_signal_ledger (organization_id, product_sku, market, captured_at DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_signal_ledger_source
    ON vkpi_signal_ledger (organization_id, source_type, captured_at DESC);

COMMENT ON TABLE vkpi_signal_ledger IS
  '外部/内部信号账本: 所有市场信号先入账再消费; dedupe_key 联合 organization_id 去重; raw_ref 只存指针不存敏感原文; summarize_for_preview 只读汇总, 表空时诚实 data_missing; 零触 viltrox_fit_score。';
COMMIT;
