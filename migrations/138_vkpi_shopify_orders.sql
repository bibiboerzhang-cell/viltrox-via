-- 138: V-KPI Shopify 订单 ingest 落地表 (Attributed GMV / ROI 真数据源)。
-- 在拿到 live Shopify creds 之前,这张表保持空 —— Dashboard 的 Attributed GMV / ROI
-- 因此继续诚实地显示「待接入」(account_picker._build_attributed_gmv_roi 在表为空时
-- 走原 honest 路径,绝不编数)。一旦 ingest 路由(POST /api/admin/vkpi/shopify/orders)
-- 把真订单写进来,summarize_gmv() 即用真 total_price_cents 求和,GMV 自动变真。
--
-- 与已有的 034_vkpi_shopify_order_snapshots 物理隔离:那张是 webhook 快照原文;
-- 这张是归一化后、按 (shop_domain, order_id) 幂等去重的 ingest 账本,带 KOL 归因列。
-- 与 KOL Pool / 评分域物理隔离;绝不碰 viltrox_fit_score / rule_v0。

CREATE TABLE IF NOT EXISTS vkpi_shopify_orders (
    id BIGSERIAL PRIMARY KEY,
    shop_domain TEXT NOT NULL,
    order_id TEXT NOT NULL,
    total_price_cents BIGINT NOT NULL DEFAULT 0,
    currency TEXT,
    financial_status TEXT,
    discount_code TEXT,
    customer_email TEXT,
    line_items_json JSONB,
    attributed_kol_pool_id BIGINT,           -- NULL 直到归因(映射 vkpi_kol_pool.id)
    ordered_at TIMESTAMPTZ,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (shop_domain, order_id)
);

-- order_id 查询(webhook/补抓按单 id 定位);ordered_at 走窗口聚合(window_days);
-- discount_code 是 KOL 折扣码归因的过滤键(summarize_gmv discount_codes 过滤)。
CREATE INDEX IF NOT EXISTS idx_vkpi_shopify_orders_order_id ON vkpi_shopify_orders(order_id);
CREATE INDEX IF NOT EXISTS idx_vkpi_shopify_orders_ordered_at ON vkpi_shopify_orders(ordered_at);
CREATE INDEX IF NOT EXISTS idx_vkpi_shopify_orders_discount_code ON vkpi_shopify_orders(discount_code);

COMMENT ON TABLE vkpi_shopify_orders IS 'V-KPI Shopify ingest ledger - normalized orders, idempotent on (shop_domain, order_id); real source for Attributed GMV / ROI. Empty until live creds -> dashboard stays honest 待接入.';
