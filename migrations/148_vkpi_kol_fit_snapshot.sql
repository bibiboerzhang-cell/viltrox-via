-- 148 V6 Fit Top 历史快照 —— 每日存 KOL Pool 的 fit_score + followers,供 diff 出 Top Movers。
-- 红线:本表只【读】 vkpi_kol_pool.viltrox_fit_score 另存一份历史,**绝不写回源列**,
--       指纹 SUM(viltrox_fit_score)=32828.726 不受影响。零 LLM、零 provider、零源写入。
-- 回滚见 148_vkpi_kol_fit_snapshot_down.sql。

CREATE TABLE IF NOT EXISTS vkpi_kol_fit_snapshot (
    id              BIGSERIAL   PRIMARY KEY,
    snapshot_date   DATE        NOT NULL,
    kol_pool_id     BIGINT      NOT NULL,
    pool_uid        TEXT,
    platform        TEXT,
    handle          TEXT,
    display_name    TEXT,
    fit_score       NUMERIC,
    followers       BIGINT,
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (snapshot_date, kol_pool_id)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_fit_snapshot_date ON vkpi_kol_fit_snapshot (snapshot_date);
CREATE INDEX IF NOT EXISTS idx_vkpi_kol_fit_snapshot_kol  ON vkpi_kol_fit_snapshot (kol_pool_id);

COMMENT ON TABLE vkpi_kol_fit_snapshot IS
    'V6 Fit Top 每日快照(只读历史):fit_score/followers 逐日存档,供 Top Movers diff;绝不写回 vkpi_kol_pool。';

-- 注册每日快照调度任务。risk_level=low(只读、零成本、零 LLM);用户 2026-06-15 明确授权
-- 「每天自动」→ enabled=TRUE。ON CONFLICT DO NOTHING 保证幂等,不覆盖运营已调过的开关。
INSERT INTO scheduler_tasks (task_key, label, enabled, risk_level) VALUES
  ('vkpi_fit_snapshot', 'V6 Fit 每日快照(只读,算 Top Movers)', TRUE, 'low')
ON CONFLICT (task_key) DO NOTHING;
