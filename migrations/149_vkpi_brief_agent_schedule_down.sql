-- 回滚 149:注销简报 Agent 调度任务(不影响 artifact / dashboard 读取)。
DELETE FROM scheduler_tasks WHERE task_key = 'vkpi_brief_agent';
