CREATE TABLE IF NOT EXISTS job_execution_ledger (
    id BIGSERIAL PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE,
    job_type TEXT NOT NULL,
    submission_id BIGINT NOT NULL DEFAULT 0,
    user_id BIGINT NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'queued',
    retry_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    detection_status TEXT DEFAULT '',
    stage TEXT DEFAULT '',
    stream_id TEXT DEFAULT '',
    consumer_name TEXT DEFAULT '',
    result_path TEXT DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    stats_json TEXT NOT NULL DEFAULT '{}',
    extra_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_job_execution_ledger_status_updated
    ON job_execution_ledger(status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_job_execution_ledger_submission_created
    ON job_execution_ledger(submission_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_job_execution_ledger_user_created
    ON job_execution_ledger(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_job_execution_ledger_job_type_created
    ON job_execution_ledger(job_type, created_at DESC);
