-- 回滚 151:注销 Signals & Alerts 刷新调度任务(不影响 artifact/展示)。
DELETE FROM scheduler_tasks WHERE task_key = 'vkpi_market_signal_refresh';
