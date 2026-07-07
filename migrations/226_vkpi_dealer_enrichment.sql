-- 226_vkpi_dealer_enrichment.sql — Dealer/零售增强表(C3 波,全景规格 5.2 节 E 表照单落地)。
-- 背景:vkpi_dealers(迁移 144)只有地理列(name/address/city/state/lat/lng),
--   Dealer 不只是名单;要能评估地区、品类、渠道、库存、合作状态、线下转化。本表把这些
--   增强字段挂在 dealer_id 上,与地理主表物理分离(不做 FK 免耦合,照抄 217 口径)。
-- 数据等 sync:本地 vkpi_dealers 0 行(硬件品牌盲区),本表也随之为空 —— 代码 + 评分先就位,
--   数据到位即用;域层读回零行诚实空态,绝不编数。
-- organization_id 为商业化前多租户安全字段,当前 Viltrox 单租户缺省 viltrox。
-- 字段:
--   id                     BIGSERIAL 主键        —— 增强行自增 id。
--   organization_id        TEXT 缺省 viltrox     —— 多租户安全字段。
--   dealer_id              BIGINT NOT NULL       —— 桥接 vkpi_dealers.id(不做 FK 免耦合)。
--   market                 TEXT                  —— 目标市场(如 US / JP)。
--   region                 TEXT                  —— 大区(North America / Europe / APAC 等)。
--   city                   TEXT                  —— 门店城市(冗余留痕,便于增强侧独立填写)。
--   product_family_fit     JSONB 缺省 空对象      —— 产品家族到适配值的映射(lens/cine/lighting...)。
--   channel_type           TEXT                  —— 渠道类型(retail / marketplace / online 等)。
--   website_url            TEXT                  —— 门店官网。
--   marketplace_url        TEXT                  —— 电商 marketplace 链接。
--   contact_status         TEXT                  —— 联系状态(cold / contacted / responded / partner 等)。
--   inventory_signal       TEXT                  —— 库存信号(in_stock / low / none / unknown)。
--   estimated_reach        DOUBLE PRECISION      —— 触达估计(线下客流或曝光量级先验)。
--   response_rate          DOUBLE PRECISION      —— 回复率(0..1)。
--   conversion_proxy       DOUBLE PRECISION      —— 线下转化代理值(0..1,非真实 sell-through)。
--   last_contacted_at      TIMESTAMPTZ           —— 最近一次联系时刻。
--   last_order_at          TIMESTAMPTZ           —— 最近一次下单时刻。
--   source_quality_score   DOUBLE PRECISION      —— 增强数据来源可信度(0..1)。
--   updated_at             TIMESTAMPTZ 缺省 NOW  —— 行更新时刻(UPSERT 刷新)。
-- additive、幂等(IF NOT EXISTS);(organization_id, dealer_id) 联合唯一支撑幂等 UPSERT。
-- 注释零 ASCII 问号、零百分号(避 compat 占位符炸 apply 的陷阱)。
-- 红线:纯增强账本,绝不触 viltrox_fit_score、不碰 rule_v0。
-- 回滚见 226_vkpi_dealer_enrichment_down.sql(DROP TABLE IF EXISTS)。
BEGIN;
CREATE TABLE IF NOT EXISTS vkpi_dealer_enrichment (
    id                   BIGSERIAL PRIMARY KEY,
    organization_id      TEXT NOT NULL DEFAULT 'viltrox',
    dealer_id            BIGINT NOT NULL,
    market               TEXT,
    region               TEXT,
    city                 TEXT,
    product_family_fit   JSONB NOT NULL DEFAULT '{}'::jsonb,
    channel_type         TEXT,
    website_url          TEXT,
    marketplace_url      TEXT,
    contact_status       TEXT,
    inventory_signal     TEXT,
    estimated_reach      DOUBLE PRECISION,
    response_rate        DOUBLE PRECISION,
    conversion_proxy     DOUBLE PRECISION,
    last_contacted_at    TIMESTAMPTZ,
    last_order_at        TIMESTAMPTZ,
    source_quality_score DOUBLE PRECISION,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (organization_id, dealer_id)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_dealer_enrichment_market
    ON vkpi_dealer_enrichment (organization_id, market);

CREATE INDEX IF NOT EXISTS idx_vkpi_dealer_enrichment_updated
    ON vkpi_dealer_enrichment (organization_id, updated_at DESC);

COMMENT ON TABLE vkpi_dealer_enrichment IS
  'Dealer 零售增强表(全景 5.2 E): 地区/品类适配/渠道/库存/联系/线下转化代理挂在 dealer_id 上, 与 vkpi_dealers 地理主表分离(无 FK); (organization_id, dealer_id) 幂等 UPSERT; conversion_proxy 是代理值非真实 sell-through; 零触 viltrox_fit_score。';
COMMIT;
