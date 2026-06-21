-- 175_down — 回滚:重新关闭 Auto-Ops 每日行动建议 cron 闸门(回到灰度默认 enabled=FALSE)。
-- 只翻 enabled 标记,scheduler 注册不动(job 体内 gate 守卫,关闸即空跑返回)。
BEGIN;
UPDATE scheduler_tasks
   SET enabled = FALSE, updated_at = NOW()
 WHERE task_key = 'daily_action_inbox_generate';
COMMIT;
