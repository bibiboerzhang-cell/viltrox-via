-- 10E failed-pool routing: add a terminal 'triage' status for non-retryable
-- failures (no_data / auth / content blocked / unsupported url / code error).
--
-- The worker classifies each failure (see app.workers.apify_jobs_worker_helpers
-- _failure_disposition) and routes retryable ones back to 'queued' while parking
-- the non-retryable ones in 'triage' for human review. 'triage' rows are inert to
-- the claim loop (it only claims status='queued'), so they will not be re-run.
--
-- Pure additive: widen the CHECK constraint only. No data is rewritten and no
-- existing status value changes.

ALTER TABLE apify_jobs
  DROP CONSTRAINT IF EXISTS chk_apify_jobs_status;

ALTER TABLE apify_jobs
  ADD CONSTRAINT chk_apify_jobs_status
  CHECK (status IN ('queued', 'running', 'done', 'failed', 'blocked', 'triage'));

-- Cheap lookup for the human-review queue and ops dashboards: list triage rows
-- newest-first, partial so it only covers the (small) parked set.
CREATE INDEX IF NOT EXISTS idx_apify_jobs_triage_updated
  ON apify_jobs (updated_at DESC)
  WHERE status = 'triage';
