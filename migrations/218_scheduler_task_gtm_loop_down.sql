-- 218 down — 注销 GTM 裁决闭环两条 cron 闸门。
BEGIN;
DELETE FROM scheduler_tasks WHERE task_key IN ('vkpi_gtm_spawn_verdicts', 'vkpi_gtm_windows_refresh');
COMMIT;
