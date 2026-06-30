-- Revert 201: drop the triage index and narrow the status CHECK back to the
-- pre-triage set. Any rows still parked in 'triage' would violate the narrowed
-- constraint, so move them back to 'failed' first.

DROP INDEX IF EXISTS idx_apify_jobs_triage_updated;

UPDATE apify_jobs
  SET status = 'failed', updated_at = NOW()
  WHERE status = 'triage';

ALTER TABLE apify_jobs
  DROP CONSTRAINT IF EXISTS chk_apify_jobs_status;

ALTER TABLE apify_jobs
  ADD CONSTRAINT chk_apify_jobs_status
  CHECK (status IN ('queued', 'running', 'done', 'failed', 'blocked'));
