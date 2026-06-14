-- 121(收口路①-2 内容契合入队:kol_content_fit_analysis job 的去重/队列探测索引)。
-- content_fit_enqueue 入队前要查「同 session 是否已有 queued/running 的内容契合 job」去重,
-- 探测条件 = job_type='kol_content_fit_analysis' AND status IN (queued/running/retrying)
--   AND payload->>'search_session_id'=?。本迁移为该探测加一条 partial 索引,纯加速。
-- 复用既有 apify_jobs 表,无新表、无新列;新 job_type 不需 schema 变更(payload 是 JSONB)。
-- 红线:additive only;零触 viltrox_fit_score / 任何评分列;回滚 DROP INDEX 即可。
-- 注:119 被 product_persona、120 被 content_fit cache index 占用,本迁移顺延 121(下一空号)。
CREATE INDEX IF NOT EXISTS idx_apify_jobs_kol_content_fit
  ON apify_jobs ((payload->>'search_session_id'), status)
  WHERE job_type = 'kol_content_fit_analysis';

COMMENT ON INDEX idx_apify_jobs_kol_content_fit IS
  '收口路①-2 内容契合入队去重/队列探测索引;独立 kol 命名空间,零触 viltrox_fit_score。回滚:DROP INDEX IF EXISTS idx_apify_jobs_kol_content_fit;';
