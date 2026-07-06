-- 211 回滚:删 KOL 报价台账表。注意:人工录入的真实报价会随表删除,回滚前如需保留请先自行导出;
-- estimate_rate 在表缺失时自动走 CPM 基准兜底(cpm_benchmark_v0),接口不炸。
BEGIN;
DROP TABLE IF EXISTS vkpi_kol_rates;
COMMIT;
