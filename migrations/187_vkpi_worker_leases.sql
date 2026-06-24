-- 187(I2 Worker Lease 分布式任务租约:云发本地领)。
-- 中心侧登记一条 leased 行(发任务),本地 worker 凭 lease_token 续租/完成/释放;
-- 过期清扫把 expires_at 早于当前时刻且仍 leased 的行置 expired,供重派。
-- additive only、幂等;红线:零触 viltrox_fit_score / rule_v0 / KOL Pool 评分。
-- 回滚见 187_vkpi_worker_leases_down.sql(DROP TABLE IF EXISTS)。
--
-- 字段:
--   id           BIGSERIAL 主键        —— 租约自增 id(与 worker_id/task_kind 一起拼 lease_token)。
--   worker_id    TEXT                  —— 领取者逻辑 worker 名。
--   task_kind    TEXT                  —— 任务种类(如 video_download / vertex_batch)。
--   lease_token  TEXT 唯一             —— 租约凭据(worker_id+task_kind+id 的 hash,可复现非随机)。
--   status       TEXT 缺省 leased      —— leased / completed / expired / released。
--   payload_ref  TEXT                  —— 入参引用(指向外部 payload,不内联大对象)。
--   result_ref   TEXT                  —— 结果引用(完成时回填)。
--   leased_at    TIMESTAMPTZ 缺省 NOW  —— 领取时刻。
--   expires_at   TIMESTAMPTZ           —— 到期时刻(过期清扫基准)。
--   updated_at   TIMESTAMPTZ 缺省 NOW  —— 行更新戳。

CREATE TABLE IF NOT EXISTS vkpi_worker_leases (
    id          BIGSERIAL PRIMARY KEY,
    worker_id   TEXT NOT NULL,
    task_kind   TEXT NOT NULL,
    lease_token TEXT,
    status      TEXT NOT NULL DEFAULT 'leased',
    payload_ref TEXT,
    result_ref  TEXT,
    leased_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_vkpi_worker_leases_token
    ON vkpi_worker_leases (lease_token);

CREATE INDEX IF NOT EXISTS idx_vkpi_worker_leases_status_expires
    ON vkpi_worker_leases (status, expires_at);

COMMENT ON TABLE vkpi_worker_leases IS
  'I2 Worker Lease 分布式任务租约: 中心登记 leased 行, 本地 worker 凭 lease_token 续租/完成/释放; 过期清扫置 expired 供重派。零触 viltrox_fit_score。';
