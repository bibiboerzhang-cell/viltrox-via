CREATE TABLE IF NOT EXISTS apify_jobs (
  id BIGSERIAL PRIMARY KEY,
  job_type TEXT,
  payload JSONB,
  status TEXT NOT NULL DEFAULT 'queued',
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  CONSTRAINT chk_apify_jobs_status
    CHECK (status IN ('queued', 'running', 'done', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_apify_jobs_status_created
  ON apify_jobs (status, created_at);

COMMENT ON TABLE apify_jobs IS
  'Persistent Apify job queue. Workers should claim queued rows with FOR UPDATE SKIP LOCKED.';
