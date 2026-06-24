-- 188 down — 注销「关注 KOL 自动轮询」cron 闸门。
BEGIN;
DELETE FROM scheduler_tasks WHERE task_key = 'kol_auto_poll';
COMMIT;
