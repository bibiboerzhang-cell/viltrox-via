-- 221 回滚:删预测评估账本表。
-- 注意:评估记录会随表删除,回滚前如需留档请先自行导出;
-- vkpi_prediction_runs 不受影响(预测原始记录仍在,可重跑评估回填)。
BEGIN;
DROP TABLE IF EXISTS vkpi_prediction_evals;
COMMIT;
