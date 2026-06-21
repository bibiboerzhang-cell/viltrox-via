-- 177_down — 回滚:移除 fulfillment_due_scan 注册行(job 体内 gate 守卫,行没了即默认不跑)。
BEGIN;
DELETE FROM scheduler_tasks WHERE task_key = 'fulfillment_due_scan';
COMMIT;
