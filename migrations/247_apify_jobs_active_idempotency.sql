-- 247: make active apify_jobs enqueue race-safe before multi-process scale-out.
-- Only active rows are unique. Terminal history remains repeatable.
CREATE UNIQUE INDEX IF NOT EXISTS uq_apify_jobs_active_idempotency
  ON apify_jobs (idempotency_key)
  WHERE idempotency_key IS NOT NULL
    AND idempotency_key <> ''
    AND status IN ('queued', 'running');

COMMENT ON INDEX uq_apify_jobs_active_idempotency IS
  'Race-safe active enqueue dedupe; terminal history does not block a later run.';
