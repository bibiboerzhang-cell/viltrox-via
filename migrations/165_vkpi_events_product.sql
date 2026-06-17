-- 165_vkpi_events_product.sql (活动设产品:vkpi_events 加产品字段)
-- 用户:活动也要能设产品,这样活动里 KOL 的追踪链能落到对应产品页(像项目那样)。
-- additive/幂等;与 KOL 评分域物理隔离:无 viltrox_fit_score / rule_v0 触点。

BEGIN;

ALTER TABLE vkpi_events ADD COLUMN IF NOT EXISTS product_sku  TEXT NOT NULL DEFAULT '';
ALTER TABLE vkpi_events ADD COLUMN IF NOT EXISTS product_name TEXT NOT NULL DEFAULT '';

COMMENT ON COLUMN vkpi_events.product_name IS '活动主推产品名(给活动 KOL 出产品页追踪链 {store}/products/{handle}?ref= 用,经 GOAFFPRO find_product_handle 解析)。';

COMMIT;
