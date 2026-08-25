-- 302 回滚:摘掉记账列与任务种子。任务本体是 config-gated 且默认 OFF,
-- 回滚后调度器读不到这一行 → _scheduler_task_enabled 返回 default(False)→ 照样不跑。
-- 会话项 payload 里已写下的 followup 记账保持原样(那是事实,不随迁移删)。
-- The migration runner owns the surrounding transaction and advisory lock. Do not add BEGIN/COMMIT here.

DELETE FROM scheduler_tasks WHERE task_key = 'vkpi_session_stuck_item_followup';

ALTER TABLE scheduler_tasks DROP COLUMN IF EXISTS last_run_summary;
