-- 188_scheduler_task_kol_auto_poll.sql — D3:注册「关注 KOL 自动轮询」cron 闸门。
-- job_kol_auto_poll 调度器每日入队轻量 metadata 刷新:对收藏/已合作/高价值/进项目
-- 且有 last_seen_at 但超过 24 小时未更新的 KOL,best-effort 入队 kol_auto_poll 作业。
-- 队列缺失时 enqueue 自身诚实返回候选清单是否可用,不报错。默认 OFF,运营在 Ops 页显式开。
-- 幂等:ON CONFLICT(task_key) DO NOTHING(沿用 177/185 同款)。零触 viltrox_fit_score。
BEGIN;
INSERT INTO scheduler_tasks (task_key, label, risk_level) VALUES
    ('kol_auto_poll', '关注KOL自动轮询(每日轻量刷收藏/高价值 metadata)', 'low')
ON CONFLICT (task_key) DO NOTHING;
COMMIT;
