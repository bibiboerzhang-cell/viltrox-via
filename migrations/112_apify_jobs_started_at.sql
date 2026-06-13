-- 112(2026-06-12,诊断 P1-1 a根治):apify_jobs 加 started_at。
-- worker claim 置 running 时写入(_claim_job),供 queue_view 算"真处理时长"
-- (updated_at - started_at)而非墙钟(updated_at - created_at,含排队等待)。
-- 墙钟被数天前队列积压污染成天文数字(video 均时≈7.8天),致 ETA「约X分」爆表(实测 3.9 天谎言)。
-- 历史 done 行 started_at 为 NULL,queue_view 用 WHERE started_at IS NOT NULL 排除,
-- 新行累积后 ETA 自愈;无样本回退 DEFAULT_JOB_DURATION_SEC(300s),永不再现墙钟天文数。
-- down:ALTER TABLE apify_jobs DROP COLUMN started_at;
ALTER TABLE apify_jobs ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
