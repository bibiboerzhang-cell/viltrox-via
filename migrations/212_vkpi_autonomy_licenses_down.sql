-- 212 回滚:删 Agent 自治驾照表。注意:人工调级与自动升降的最近变更记录会随表删除,
-- 回滚前如需留档请先自行导出;autonomy_license.py 在表缺失时诚实降级
-- (current_level 回 L0 观察态、evaluate_promotions 回 empty 并说明原因),接口不炸。
BEGIN;
DROP TABLE IF EXISTS vkpi_autonomy_licenses;
COMMIT;
