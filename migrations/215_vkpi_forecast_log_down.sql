-- 215 回滚:删预测流水台账表。
-- 注意:已积累的预测流水与对答案结果(actual_views / outcome)会随表删除,
-- 回滚前如需留档请先自行导出;performance_forecast 落库钩子在表缺失时
-- best-effort 静默降级(只警告不炸接口),learning.forecast_feedback 诚实回 empty。
BEGIN;
DROP TABLE IF EXISTS vkpi_forecast_log;
COMMIT;
