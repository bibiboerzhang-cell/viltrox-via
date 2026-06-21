-- 175_enable_daily_action_inbox_generate.sql — R5:开 Auto-Ops 每日行动建议 cron 闸门。
-- task_key 'daily_action_inbox_generate' 在 141 已登记(enabled=FALSE 灰度 low 档)。
-- job_daily_action_inbox_generate 已在 scheduler 注册(07:30 中国时区 cron),但 gate 此前关 →
-- _scheduler_task_enabled 恒 False → 永不自动生成。本迁移把闸门显式打开:闸开后每天 07:30
-- 自动聚合 8 类待办建议(dry-run only:只产建议、不执行、不写业务表、零触 viltrox_fit_score)。
-- 真执行仍走 W2 人审 + 双闸(approve→execute)。幂等:WHERE 命中即更新;回滚见 _down.sql。
BEGIN;
UPDATE scheduler_tasks
   SET enabled = TRUE, updated_at = NOW()
 WHERE task_key = 'daily_action_inbox_generate';
COMMIT;
