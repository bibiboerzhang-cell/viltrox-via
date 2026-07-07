-- 226 回滚:删 Dealer 零售增强表。
-- 注意:增强字段(地区/品类适配/联系状态/转化代理)会随表删除,回滚前如需留档请先自行导出;
-- dealer_enrichment 域层在表缺失时诚实降级(读回空、写回 table_missing,不炸),
-- vkpi_dealers 地理主表不受影响。
BEGIN;
DROP TABLE IF EXISTS vkpi_dealer_enrichment;
COMMIT;
