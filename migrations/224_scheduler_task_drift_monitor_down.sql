-- 224_scheduler_task_drift_monitor_down.sql — 回滚:删漂移哨兵闸门种子行。
BEGIN;
DELETE FROM scheduler_tasks WHERE task_key = 'vkpi_drift_monitor';
COMMIT;
