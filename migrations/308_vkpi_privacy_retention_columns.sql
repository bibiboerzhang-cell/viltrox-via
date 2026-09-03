-- 308: S-08 / S-09 隐私加固——只加列/索引,不改既有数据语义。
--
-- 一、门户 token 过期(S-08):vkpi_kol_portal_tokens 加 expires_at。
--   * 新签发的 token 由 app.domains.kol.portal.issue_token 写 now + VKPI_PORTAL_TOKEN_TTL_DAYS(默认 90 天);
--   * 老行保持 NULL——读端(resolve_token)对 NULL 按 created_at + 90 天判过期,本迁移不回填、不改语义。
--   * token 列自本版起存 sha256$ 摘要(入库前哈希,由代码层完成;列类型/约束不变,老明文行到期或轮换后自然消失)。
--
-- 二、Apify 原始 payload 保留期(S-09):apify_jobs 加 payload_purged_at。
--   * 每日任务 vkpi_data_retention_purge(默认 dry-run 只报数;env VKPI_DATA_RETENTION_PURGE=1 放量)
--     对「终态(done/failed/blocked)且 created_at 早于 90 天」的行把 payload 置 NULL 并盖章 payload_purged_at。
--   * NULL = 尚未清理;非 NULL = 该行 payload 已按保留期清空(不是「没有过 payload」)。
--
-- 三、保留期扫描索引(S-09):评论两表按时间列建普通索引,避免每日全表扫。
-- 红线:不含 fit 分、不碰 viltrox_fit_score / rule_v0;不 DELETE、不 UPDATE 既有数据。

ALTER TABLE vkpi_kol_portal_tokens
  ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ NULL;

COMMENT ON COLUMN vkpi_kol_portal_tokens.expires_at IS
    'S-08: portal token expiry (default now+90d at issue time, env VKPI_PORTAL_TOKEN_TTL_DAYS). NULL = legacy row issued before 308, readers treat NULL as created_at + 90 days. token column stores sha256$ digest since this version.';

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_portal_tokens_expires
    ON vkpi_kol_portal_tokens(expires_at)
    WHERE revoked = FALSE;

ALTER TABLE apify_jobs
  ADD COLUMN IF NOT EXISTS payload_purged_at TIMESTAMPTZ NULL;

COMMENT ON COLUMN apify_jobs.payload_purged_at IS
    'S-09: set when the daily retention job cleared payload (raw provider input/output) after the 90-day window. NULL means not yet purged, never "no payload".';

CREATE INDEX IF NOT EXISTS idx_apify_jobs_retention_candidates
    ON apify_jobs(created_at)
    WHERE payload_purged_at IS NULL AND status IN ('done', 'failed', 'blocked');

CREATE INDEX IF NOT EXISTS idx_vkpi_comments_fetched_at
    ON vkpi_comments(fetched_at);

CREATE INDEX IF NOT EXISTS idx_kol_comments_created_at
    ON kol_comments(created_at);
