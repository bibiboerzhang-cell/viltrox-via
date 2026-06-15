-- 130 回滚:删除调度任务注册表。
-- 仅删除本迁移新建的 scheduler_tasks 表,既有表/列(viltrox_fit_score / rule_v0 等)不受影响。
-- main.py 的 _trust_scheduler() 有 table_exists 守卫,删表后自动回到 "not_configured"。
DROP TABLE IF EXISTS scheduler_tasks;
