-- 121 回滚:仅 DROP 索引,无表/列变更需逆转。
DROP INDEX IF EXISTS idx_apify_jobs_kol_content_fit;
