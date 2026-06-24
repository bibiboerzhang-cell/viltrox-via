-- 185_scheduler_task_logistics_track_sync.sql — E2:注册 17track 物流自动同步 cron 闸门。
-- job_logistics_track_sync 调度器每 2 小时入队 17track 同步(全部在途单号),delivered 后触发观察窗。
-- 需 VKPI_17TRACK_TOKEN(免费注册制);无 token 时入队自身诚实返回 blocked。默认 OFF,运营 Ops 页显式开。
-- 幂等:ON CONFLICT(task_key) DO NOTHING(沿用 177 同款)。零触 viltrox_fit_score。
BEGIN;
INSERT INTO scheduler_tasks (task_key, label, risk_level) VALUES
    ('logistics_track_sync', '物流自动同步(17track,每 2 小时刷在途单号 → delivered 触发观察窗)', 'low')
ON CONFLICT (task_key) DO NOTHING;
COMMIT;
