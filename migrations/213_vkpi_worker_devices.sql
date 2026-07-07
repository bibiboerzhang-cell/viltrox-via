-- 213_vkpi_worker_devices.sql — 本地算力 Worker 设备注册与短期任务租约(W1 地基件)。
-- 背景:本地 worker 永不持有长期 API key,一切凭「短期任务 token」;
--   token 为 HMAC-SHA256(secret, lease_id+job_id+expires_at) 的 hex,签发时只存 token_hash=sha256(token),
--   submit 必验 token+未过期+lease 状态。本地上传结果一律先落 staging(result_json),
--   服务端校验(hash/结构/URL匹配)通过后才可信;本地路径绝不直写核心业务表
--   (evidence/kol_pool/分数列),落库由服务端校验桥走既有函数。
-- 安全任务类型 v0 仅四类可租:video_precheck / metadata_extract / download_frames / comment_clean,
--   白名单硬编码在 app/domains/local_workers/registry.py,库层不放开。
-- 红线:零触 viltrox_fit_score、零触 rule_v0;本迁移 additive、幂等(IF NOT EXISTS);
--   注释零 ASCII 问号、零百分号(避 compat 占位符陷阱)。
-- 回滚见 213_vkpi_worker_devices_down.sql(DROP TABLE IF EXISTS)。
--
-- vkpi_worker_devices 字段:
--   id            SERIAL 主键            —— 设备行自增 id。
--   device_id     TEXT 唯一              —— 设备逻辑 id(注册时生成,后续心跳/领活凭它)。
--   staff_id      INT                    —— 归属员工(诚实登记谁的机器,不做 FK 免耦合)。
--   device_name   TEXT                   —— 设备名(如 macbook-of-bibi)。
--   platform      TEXT                   —— 平台(darwin / linux / win32)。
--   capabilities  JSONB                  —— 设备能力声明+心跳运行时信息(_runtime 键)。
--   last_seen_at  TIMESTAMPTZ            —— 最近心跳时刻(在线态判定基准)。
--   status        TEXT 缺省 offline      —— offline / online(过期清扫置回 offline)。
--   trust_level   INT 缺省 0             —— 信任级别,0=最低,只许领四类安全任务。
--   created_at    TIMESTAMPTZ 缺省 NOW   —— 注册时刻。
--
-- vkpi_local_task_leases 字段:
--   id               SERIAL 主键         —— 租约自增 id(参与 token 签名)。
--   job_id           INT                 —— 对应 apify_jobs.id(绝不改 apify_jobs 表结构,
--                                           claimed 标记走 payload 的 local_lease_id 键)。
--   device_id        TEXT                —— 领取设备。
--   task_type        TEXT                —— 任务类型(仅四类安全白名单)。
--   status           TEXT 缺省 leased    —— leased / submitted / rejected / expired。
--   token_hash       TEXT                —— sha256(task_token),绝不存 token 明文。
--   issued_at        TIMESTAMPTZ 缺省 NOW —— 签发时刻。
--   expires_at       TIMESTAMPTZ         —— 到期时刻(过期即拒收+回收重派)。
--   submitted_at     TIMESTAMPTZ         —— 提交时刻。
--   result_json      JSONB               —— staging 结果(未经校验不可信,不直写业务表)。
--   result_validated INT 缺省 0          —— 服务端校验通过置 1(int 非 boolean,避 compat 读回陷阱)。
--   validation_notes TEXT                —— 校验备注(通过/拒绝原因,诚实留痕)。
--   error_code       TEXT                —— 错误码(lease_expired / invalid_result 等)。
BEGIN;
CREATE TABLE IF NOT EXISTS vkpi_worker_devices (
    id           SERIAL PRIMARY KEY,
    device_id    TEXT UNIQUE,
    staff_id     INT,
    device_name  TEXT,
    platform     TEXT,
    capabilities JSONB,
    last_seen_at TIMESTAMPTZ,
    status       TEXT DEFAULT 'offline',
    trust_level  INT DEFAULT 0,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vkpi_worker_devices_status_seen
    ON vkpi_worker_devices (status, last_seen_at DESC);

CREATE TABLE IF NOT EXISTS vkpi_local_task_leases (
    id               SERIAL PRIMARY KEY,
    job_id           INT,
    device_id        TEXT,
    task_type        TEXT,
    status           TEXT DEFAULT 'leased',
    token_hash       TEXT,
    issued_at        TIMESTAMPTZ DEFAULT NOW(),
    expires_at       TIMESTAMPTZ,
    submitted_at     TIMESTAMPTZ,
    result_json      JSONB,
    result_validated INT DEFAULT 0,
    validation_notes TEXT,
    error_code       TEXT
);

CREATE INDEX IF NOT EXISTS idx_vkpi_local_task_leases_status_expires
    ON vkpi_local_task_leases (status, expires_at);

CREATE INDEX IF NOT EXISTS idx_vkpi_local_task_leases_device
    ON vkpi_local_task_leases (device_id, status);

CREATE INDEX IF NOT EXISTS idx_vkpi_local_task_leases_job
    ON vkpi_local_task_leases (job_id);

COMMENT ON TABLE vkpi_worker_devices IS
  'W1 本地算力设备注册表: 注册/心跳/在线态; trust_level=0 只许领四类安全任务; 零触 viltrox_fit_score。';

COMMENT ON TABLE vkpi_local_task_leases IS
  'W1 短期任务租约 staging 表: token_hash 验证+过期回收; result_json 未经服务端校验不可信, 绝不直写核心业务表。';
COMMIT;
