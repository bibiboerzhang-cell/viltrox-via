-- 177_scheduler_task_fulfillment_due_scan.sql — R10:注册履约「到期未发布」扫描 cron 闸门。
-- job_fulfillment_due_scan 调度器每天跑(本迁移落地后于 jobs.py 注册),按 7/14/21 天 days_overdue
-- 调 fulfillment_observation.scan_due_into_tasks → 为「已签收满 N 天仍零内容」的项目 CREATE 一条
-- content_due 待人核任务(零自动裁决、零触 viltrox_fit_score)。默认 OFF,运营在 Ops 页显式开。
-- 幂等:ON CONFLICT(task_key) DO NOTHING(沿用 167/168/172 同款)。
BEGIN;
INSERT INTO scheduler_tasks (task_key, label, risk_level) VALUES
    ('fulfillment_due_scan', '履约到期未发布扫描(7/14/21 天 → content_due 待办)', 'low')
ON CONFLICT (task_key) DO NOTHING;
COMMIT;
