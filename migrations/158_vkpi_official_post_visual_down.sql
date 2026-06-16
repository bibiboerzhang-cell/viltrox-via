-- 158 回滚:删表 + 撤 seed。
DROP TABLE IF EXISTS vkpi_official_post_visual;
DELETE FROM vkpi_provider_budget_caps WHERE scope = 'cron:official_visual';
DELETE FROM scheduler_tasks WHERE task_key = 'vkpi_official_visual_scan';
