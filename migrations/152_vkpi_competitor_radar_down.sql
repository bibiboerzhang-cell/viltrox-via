DELETE FROM scheduler_tasks WHERE task_key = 'vkpi_competitor_radar';
DELETE FROM vkpi_provider_budget_caps WHERE scope = 'cron:competitor_radar';
DROP TABLE IF EXISTS vkpi_competitor_radar;
