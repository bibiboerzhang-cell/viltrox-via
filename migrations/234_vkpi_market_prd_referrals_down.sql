-- 234 回滚:撤销市场之声「转产品部」PRD 转交账本。
-- 注意:回滚即删表(转交行随之丢失,属该功能自身账本,不影响任何上游声音数据);
--   回滚后 POST /market/prd-referrals 将诚实报 prd_referrals_table_missing(400),
--   前端「已转产品部」KPI 回落诚实空态,绝不编数。
BEGIN;

DROP TABLE IF EXISTS vkpi_market_prd_referrals;

COMMIT;
