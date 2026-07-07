-- 222 down — 注销推论点火三条 cron 闸门。
BEGIN;
DELETE FROM scheduler_tasks WHERE task_key IN ('vkpi_forecast_outcomes_refresh', 'vkpi_prediction_weekly_rollup', 'vkpi_baseline_forecast_daily');
COMMIT;
