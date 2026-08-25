-- 302: 卡住会话项的续补任务(D2 车道口径:config-gate 默认 OFF、零 LLM、零 Apify)+ 调度记账列。
--
-- 背景(prod a05e48dd3 只读探针,2026-08-25):242 条 vkpi_kol_search_session_items
-- 长期停在 status='partial'(profile 143 / summary 99),其中 239 条身上连一个在跑的
-- job 都没有 —— 没有任何机制会再碰它们,永远停在半路。逐条判档:
--     210 条档案其实已就绪(profile_execute.status=ready,池行 handle/followers/avatar 三项俱全),
--          只是旧口径把「可选补全没做完」也写成了 partial;
--      29 条身份有多个候选,必须人来认;
--       3 条从未落库且 job 已 blocked 且 retry_allowed=false。
-- 续补任务只做「按已有证据结算 + 判终态 + 记账」,不打 provider、不花钱。
--
-- 本迁移纯 additive:
--   1) scheduler_tasks 种子一行,enabled=FALSE(默认关,运维开闸);
--   2) scheduler_tasks.last_run_summary —— last_status 被约束成 ok|failed|blocked 三值,
--      装不下「推进几条/终态几条」这种每轮记账,另开一列存,不动 last_status 语义。
-- 不新增任何 item/session 的 status 取值,迁移 103 / 293 的 CHECK 一个字不动。
-- 回滚见 302_vkpi_session_stuck_item_followup_down.sql。
-- The migration runner owns the surrounding transaction and advisory lock. Do not add BEGIN/COMMIT here.

ALTER TABLE scheduler_tasks ADD COLUMN IF NOT EXISTS last_run_summary TEXT NOT NULL DEFAULT '';

COMMENT ON COLUMN scheduler_tasks.last_run_summary IS
    '最近一次运行的记账明细(如 scanned=100 advanced=87 terminal=11 retry=2 sessions=34 promoted=6);last_status 只放 ok|failed|blocked,明细放这里。';

INSERT INTO scheduler_tasks (task_key, label, enabled, risk_level) VALUES
    ('vkpi_session_stuck_item_followup', '搜索会话卡住项续补(每日 05:50,零 LLM/零 Apify,只按已有证据结算并判终态)', FALSE, 'low')
ON CONFLICT (task_key) DO NOTHING;
