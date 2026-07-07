-- 224_scheduler_task_drift_monitor.sql — W9 漂移哨兵 cron 闸门(默认 OFF)。
-- job_vkpi_drift_monitor:每周一从 vkpi_prediction_evals 近两窗残差(error_abs)算漂移,
--   参照期 vs 当前期出 PSI/残差漂移落信号账本(source_type='drift_monitor');evidently 装了
--   走库漂移否则 builtin;表未建/样本荒诚实 empty 永不抛。
-- 幂等:ON CONFLICT(task_key) DO NOTHING(沿用 218/222 同款)。默认 OFF,运营在 Ops 页显式开。
-- 零触 viltrox_fit_score。回滚见 224_scheduler_task_drift_monitor_down.sql。
BEGIN;
INSERT INTO scheduler_tasks (task_key, label, risk_level) VALUES
    ('vkpi_drift_monitor', '预测残差漂移哨兵(每周一,近两窗残差PSI落信号账本)', 'low')
ON CONFLICT (task_key) DO NOTHING;
COMMIT;
