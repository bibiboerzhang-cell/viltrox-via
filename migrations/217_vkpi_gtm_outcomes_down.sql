-- 217 回滚:删 GTM 级结果总账表。
-- 注意:裁决记录(decision/lesson/next_weight_change)会随表删除,回滚前如需留档请先自行导出;
-- verdict_flow 在表缺失时诚实降级(裁决端点回明确 reason,不炸接口),
-- vkpi_action_inbox 不受影响(gtm_verdict 裁决任务行仍在,可人工 dismiss 清理)。
BEGIN;
DROP TABLE IF EXISTS vkpi_gtm_outcomes;
COMMIT;
