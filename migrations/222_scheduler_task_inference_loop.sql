-- 222_scheduler_task_inference_loop.sql — 推论点火:注册三条预测闭环 cron 闸门(默认 OFF)。
-- job_vkpi_forecast_outcomes_refresh:每日回查满窗(30 天)仍 pending 的 vkpi_forecast_log 行,
--   填实际播放中位数并判 outcome(窗口内无新发证据保持 pending,诚实等料绝不硬判)。
-- job_vkpi_prediction_weekly_rollup:每周一把已裁决预测流水幂等补账进 vkpi_prediction_evals,
--   并出周指标(wape / 带内率 / 方向命中率)落信号账本(dedupe_key 带周号,同周重跑幂等)。
-- job_vkpi_baseline_forecast_daily:每渠道日播放增量序列的经验分位数基线落 vkpi_prediction_runs
--   (渠道+UTC日幂等;样本少于 8 个点诚实不落账)。
-- 幂等:ON CONFLICT(task_key) DO NOTHING(沿用 218 同款)。默认 OFF,运营在 Ops 页显式开。
-- 零触 viltrox_fit_score。回滚见 222_scheduler_task_inference_loop_down.sql。
BEGIN;
INSERT INTO scheduler_tasks (task_key, label, risk_level) VALUES
    ('vkpi_forecast_outcomes_refresh', '预测流水对答案刷新(每日,满30天pending行回查实际判带内)', 'low'),
    ('vkpi_prediction_weekly_rollup', '预测账本周评估(每周一,补账评估+wape带内率方向命中)', 'low'),
    ('vkpi_baseline_forecast_daily', '官号日增量经验分位数基线(每日,样本不足诚实不落账)', 'low')
ON CONFLICT (task_key) DO NOTHING;
COMMIT;
