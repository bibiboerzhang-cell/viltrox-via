-- 170(Fabric 控制面第一刀·增量1,纯 additive):给 apify_jobs 加显式租约 + 幂等/内容哈希列。
-- 目标(分布式计算 fabric 地基):用 lease_owner + lease_expires_at 显式租约,替代「updated_at 年龄
-- + 心跳线程」的隐式判活 —— 消除「心跳线程死、真活还在跑 → 行被 reclaim 双跑双付费」的 race,
-- 并为多机认领区分「谁在跑」。idempotency_key / input_hash / output_hash 为后续 verify→commit
-- seam + 入队去重预留。
-- 本迁移只加列;worker 仅「写入」租约(claim 设 / heartbeat 续),暂不改 reclaim 判据(零行为变更)。
-- 增量2 再把 _reclaim_stale_running_jobs 切到 lease_expires_at + 加 idempotency 唯一约束 + verify seam。
-- additive/幂等;与 KOL 评分域物理隔离:无 viltrox_fit_score / rule_v0 触点。

ALTER TABLE apify_jobs ADD COLUMN IF NOT EXISTS lease_owner TEXT;
ALTER TABLE apify_jobs ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;
ALTER TABLE apify_jobs ADD COLUMN IF NOT EXISTS idempotency_key TEXT;
ALTER TABLE apify_jobs ADD COLUMN IF NOT EXISTS input_hash TEXT;
ALTER TABLE apify_jobs ADD COLUMN IF NOT EXISTS output_hash TEXT;

-- 未来 reclaim 走 (status, lease_expires_at);部分索引只覆盖 running 且已租出的行。
CREATE INDEX IF NOT EXISTS idx_apify_jobs_lease ON apify_jobs (status, lease_expires_at)
  WHERE status = 'running' AND lease_expires_at IS NOT NULL;

COMMENT ON COLUMN apify_jobs.lease_owner IS '显式租约持有者(worker_name:pid);多机认领时区分谁在跑。';
COMMENT ON COLUMN apify_jobs.lease_expires_at IS '租约到期戳(claim 设、heartbeat 续);增量2 起 reclaim 判 lease_expires_at<NOW()。';
COMMENT ON COLUMN apify_jobs.idempotency_key IS '入队幂等键(后续接 lock_key/sha256(payload),DB 唯一约束去重)。';
COMMENT ON COLUMN apify_jobs.input_hash IS '输入内容哈希(去重/重试幂等用)。';
COMMENT ON COLUMN apify_jobs.output_hash IS '结果内容哈希(verify→commit seam:比对后才提升进核心表,防发散结果静默覆盖)。';
