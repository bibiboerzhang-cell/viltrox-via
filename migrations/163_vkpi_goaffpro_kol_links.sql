-- 163_vkpi_goaffpro_kol_links.sql (GOAFFPRO D2:KOL↔affiliate 映射 + 已同步销售落账)
-- D1(162)只建 creds 管子;本刀(D2)落 "一键给 KOL 建 affiliate" 的映射表 + 销售归因表。
-- additive/幂等(IF NOT EXISTS);占位符在运行时由方言层翻译(本文件是 DDL,无 '?')。
-- 与 KOL 评分域物理隔离:无 viltrox_fit_score / rule_v0 触点;不改 vkpi_kol_pool 任何列。
-- SQLite 运行时由 goaffpro_connect.ensure_goaffpro_links_schema() 自建同构表;Postgres 走本迁移。
-- 回滚见 163_vkpi_goaffpro_kol_links_down.sql(仅文档/手动,编排者不自动跑)。

BEGIN;

-- ===========================================================================
-- vkpi_goaffpro_kol_links
-- "V-KPI 一键给 KOL 建 affiliate" 的本地映射:KOL(kol_pool_id) ↔ GOAFFPRO affiliate。
-- kol_pool_id UNIQUE:一个 KOL 只对一个 affiliate(重复建 = 幂等,直接复用旧映射)。
-- affiliate_id 用 TEXT 存(GOAFFPRO 返回可能是数字或字符串 id,TEXT 最宽容);
-- tracking_url = 追踪链(referral_link 或 {store}/?ref={ref_code});coupon = 优惠码(可空)。
-- kol_pool_id 指向 vkpi_kol_pool(id)(BIGSERIAL),ON DELETE CASCADE。
-- ===========================================================================
CREATE TABLE IF NOT EXISTS vkpi_goaffpro_kol_links (
    id           BIGSERIAL   PRIMARY KEY,
    kol_pool_id  BIGINT      NOT NULL UNIQUE REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
    affiliate_id TEXT        NOT NULL DEFAULT '',
    ref_code     TEXT        NOT NULL DEFAULT '',
    tracking_url TEXT        NOT NULL DEFAULT '',
    coupon       TEXT        NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 销售同步按 affiliate_id 回找 kol_pool_id(sync-sales 热路径)。
CREATE INDEX IF NOT EXISTS idx_vkpi_goaffpro_kol_links_affiliate
    ON vkpi_goaffpro_kol_links(affiliate_id);

COMMENT ON TABLE vkpi_goaffpro_kol_links IS
    'GOAFFPRO D2:KOL(kol_pool_id) ↔ affiliate 本地映射(一键建 affiliate + 追踪链 + 优惠码;KOL 零注册)。tracking_url/coupon/ref_code 字段名待真 key 响应校准。';

-- ===========================================================================
-- vkpi_goaffpro_sales
-- 已同步的 GOAFFPRO 销售/转化快照(sync-sales 落账;归因聚合读这里,不实时打 GOAFFPRO)。
-- sale_id 主键 = GOAFFPRO sale/order 唯一 id(TEXT 最宽容);ON CONFLICT(sale_id) DO UPDATE 幂等。
-- kol_pool_id 可空:affiliate_id 在映射表里找不到对应 KOL 时(unmatched)留空,仍落账留痕。
-- 金额存 *_cents(BIGINT)避免浮点;currency/status 原样存,occurred_at = GOAFFPRO 侧成交时间。
-- 不加 FK 到 vkpi_kol_pool(unmatched 行 kol_pool_id 可为孤儿,且销售是审计源应抗删)。
-- ===========================================================================
CREATE TABLE IF NOT EXISTS vkpi_goaffpro_sales (
    sale_id         TEXT        PRIMARY KEY,
    affiliate_id    TEXT        NOT NULL DEFAULT '',
    kol_pool_id     BIGINT,
    total_cents     BIGINT      NOT NULL DEFAULT 0,
    commission_cents BIGINT     NOT NULL DEFAULT 0,
    currency        TEXT        NOT NULL DEFAULT '',
    status          TEXT        NOT NULL DEFAULT '',
    occurred_at     TIMESTAMPTZ,
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 归因聚合按 affiliate_id / kol_pool_id 分组。
CREATE INDEX IF NOT EXISTS idx_vkpi_goaffpro_sales_affiliate
    ON vkpi_goaffpro_sales(affiliate_id);
CREATE INDEX IF NOT EXISTS idx_vkpi_goaffpro_sales_kol
    ON vkpi_goaffpro_sales(kol_pool_id);

COMMENT ON TABLE vkpi_goaffpro_sales IS
    'GOAFFPRO D2:已同步销售快照(归因落账)。kol_pool_id 可空=unmatched;金额 *_cents 避免浮点。字段映射待真 key 响应校准。';

COMMIT;
