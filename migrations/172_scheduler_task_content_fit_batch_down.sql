-- down 172:撤 content_fit 批量 config-gate 开关种子。
DELETE FROM scheduler_tasks WHERE task_key = 'vkpi_content_fit_batch';
