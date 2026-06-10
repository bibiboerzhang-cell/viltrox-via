ALTER TABLE apify_jobs
  ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS last_error_category TEXT;

CREATE INDEX IF NOT EXISTS idx_apify_jobs_status_next_retry
  ON apify_jobs (status, next_retry_at, created_at)
  WHERE status='queued';

CREATE INDEX IF NOT EXISTS idx_apify_jobs_error_category_updated
  ON apify_jobs (last_error_category, updated_at DESC)
  WHERE last_error_category IS NOT NULL;

COMMENT ON COLUMN apify_jobs.next_retry_at IS
  'Worker retry gate. Queued jobs with a future value are not claimable yet.';

COMMENT ON COLUMN apify_jobs.last_error_category IS
  'Coarse failure family such as provider_pressure, media_resolve, download, timeout, or blocked.';
