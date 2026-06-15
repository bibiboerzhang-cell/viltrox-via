-- 140(T5 真实 worker 存活)。apify_jobs_worker 每轮 poll(含空闲)UPSERT 一行心跳,
-- system_health._worker_online 据此判定在线(last_heartbeat 在 ~2 分钟内),空闲 worker 不再误判离线。
-- additive only、幂等;红线:零触 viltrox_fit_score / rule_v0 / KOL Pool 评分。
-- 回滚见 140_vkpi_worker_heartbeat_down.sql(DROP TABLE IF EXISTS)。
--
-- 字段:
--   worker_name      TEXT PRIMARY KEY  —— 逻辑 worker 名(默认 apify_jobs_worker),UPSERT 主键。
--   last_heartbeat_at TIMESTAMPTZ      —— 最近一次 poll 心跳时刻(在线判定基准)。
--   pid              INT               —— 进程 pid(诊断用)。
--   updated_at       TIMESTAMPTZ       —— 行更新戳。

CREATE TABLE IF NOT EXISTS vkpi_worker_heartbeat (
    worker_name       TEXT PRIMARY KEY,
    last_heartbeat_at TIMESTAMPTZ,
    pid               INT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE vkpi_worker_heartbeat IS
  'T5 真实 worker 存活: apify_jobs_worker 每轮 poll UPSERT 心跳; system_health._worker_online 据 last_heartbeat_at 判在线(空闲也心跳)。';
