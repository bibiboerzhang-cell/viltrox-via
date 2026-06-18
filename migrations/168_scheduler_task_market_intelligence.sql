-- 168_scheduler_task_market_intelligence.sql — 给「市场情报每日刷新」登记 config-gate 开关。
-- 前端设置页动态读 scheduler_tasks → 种这一条,任务即在「设置 > 定时任务」可见 + 可开关。
-- 默认 OFF(enabled 列默认 FALSE);运营在设置页显式开启才真跑。幂等:ON CONFLICT DO NOTHING。
BEGIN;
INSERT INTO scheduler_tasks (task_key, label, risk_level) VALUES
    ('vkpi_market_intelligence_refresh', '市场情报每日刷新(竞品信号→新 run)', 'medium')
ON CONFLICT (task_key) DO NOTHING;
COMMIT;
