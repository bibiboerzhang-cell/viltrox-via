-- 308 down: 回滚隐私加固的列与索引。
-- 只丢本迁移自己加的东西:expires_at / payload_purged_at 两列与四个索引;
-- 不动 token 列(摘要行与明文行都保留,读端的 IN (摘要, 原文) 兼容两种形态),不恢复已清空的 payload(保留期删除本就不可逆)。
DROP INDEX IF EXISTS idx_kol_comments_created_at;
DROP INDEX IF EXISTS idx_vkpi_comments_fetched_at;
DROP INDEX IF EXISTS idx_apify_jobs_retention_candidates;
DROP INDEX IF EXISTS idx_vkpi_kol_portal_tokens_expires;

ALTER TABLE apify_jobs
  DROP COLUMN IF EXISTS payload_purged_at;

ALTER TABLE vkpi_kol_portal_tokens
  DROP COLUMN IF EXISTS expires_at;

DELETE FROM schema_migrations
 WHERE version_key = '308_vkpi_privacy_retention_columns.sql';
