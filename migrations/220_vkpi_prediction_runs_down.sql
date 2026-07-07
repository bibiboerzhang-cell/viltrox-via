-- 220 回滚:删预测运行账本表。
-- 注意:预测记录会随表删除,回滚前如需留档请先自行导出;
-- 221 vkpi_prediction_evals 经 run_id 软关联本表,如需一并回滚请先执行 221 down。
BEGIN;
DROP TABLE IF EXISTS vkpi_prediction_runs;
COMMIT;
