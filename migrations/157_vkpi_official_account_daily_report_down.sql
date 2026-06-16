-- 157 回滚:删表 + 撤 seed(预算 cap 与调度任务)。
DROP TABLE IF EXISTS vkpi_official_account_daily_report;
DELETE FROM vkpi_provider_budget_caps WHERE scope = 'cron:official_daily_report';
DELETE FROM scheduler_tasks WHERE task_key = 'vkpi_official_daily_report';
