-- 156 回滚:仅 DROP 索引,无表/列变更需逆转。
-- (worker ORDER BY 的 CASE 逻辑回滚需同步还原 apify_jobs_worker._claim_job;索引可独立 DROP。)
DROP INDEX IF EXISTS idx_apify_jobs_claim_priority;
