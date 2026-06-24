-- 185 down — 注销物流自动同步 cron 闸门。
BEGIN;
DELETE FROM scheduler_tasks WHERE task_key = 'logistics_track_sync';
COMMIT;
