-- 回滚 150:删 AI Today 表 + 注销预算闸 + 调度任务(不影响其它)。
DELETE FROM scheduler_tasks WHERE task_key = 'vkpi_ai_today_hot';
DELETE FROM vkpi_provider_budget_caps WHERE scope = 'cron:ai_today_hot';
DROP TABLE IF EXISTS vkpi_ai_today_hot;
