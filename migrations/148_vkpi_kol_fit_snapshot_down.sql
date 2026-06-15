-- 回滚 148:删快照表 + 注销调度任务(不影响 vkpi_kol_pool / 指纹)。
DELETE FROM scheduler_tasks WHERE task_key = 'vkpi_fit_snapshot';
DROP TABLE IF EXISTS vkpi_kol_fit_snapshot;
