-- 156(刀1·并发A 交互插队):为 _claim_job 的新优先级 ORDER BY 加 partial 函数索引,纯加速。
-- worker 抢单从纯 FIFO 改为三档优先:
--   tier0 = 单条交互(payload.batch='on_demand')+ 无 batch 的链式 followup(dossier/content_fit/comments)
--   tier1 = 用户「分析全部视频」(payload.batch='on_demand_batch')
--   tier2 = 系统批量脚本(payload.batch IN ('recent','remaining'))
-- 解决「一个用户排 100 条批量,其他人单条任务被压在后面等数小时」的队头阻塞。
-- 索引表达式必须与 apify_jobs_worker._claim_job 的 ORDER BY CASE 逐字一致,Postgres 才会命中。
-- 复用既有 apify_jobs 表,无新表、无新列(batch 在 JSONB payload 里)。
-- 红线:additive only;零触 viltrox_fit_score / 任何评分列;回滚 DROP INDEX 即可。
-- 注:155 为上一空号,本迁移顺延 156。
CREATE INDEX IF NOT EXISTS idx_apify_jobs_claim_priority
  ON apify_jobs (
    (CASE
       WHEN payload->>'batch' = 'on_demand_batch' THEN 1
       WHEN payload->>'batch' IN ('recent', 'remaining') THEN 2
       ELSE 0
     END),
    COALESCE(next_retry_at, created_at),
    created_at,
    id
  )
  WHERE status = 'queued';

COMMENT ON INDEX idx_apify_jobs_claim_priority IS
  '刀1 并发A 交互插队:_claim_job 三档优先抢单索引(交互单条/followup > 用户批量 > 系统批量);零触 viltrox_fit_score。回滚:DROP INDEX IF EXISTS idx_apify_jobs_claim_priority;';
