CREATE TABLE IF NOT EXISTS vkpi_sync_runs (
    run_id TEXT PRIMARY KEY,
    job_name TEXT NOT NULL,
    stage TEXT NOT NULL,

    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,

    status TEXT NOT NULL CHECK (
        status IN ('running', 'completed', 'interrupted', 'failed')
    ),

    total_targets INT NOT NULL DEFAULT 0,
    last_success_index INT NOT NULL DEFAULT 0,

    interrupted_at_index INT,
    interrupted_kol_pool_id BIGINT,

    reason TEXT,
    error_type TEXT CHECK (
        error_type IS NULL OR error_type IN (
            'db_connection_lost',
            'provider_timeout',
            'data_field_missing',
            'other'
        )
    ),
    error_class TEXT,
    error_message TEXT,
    traceback_text TEXT,

    payload_json TEXT,
    summary_json TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON COLUMN vkpi_sync_runs.last_success_index IS
'Last target index that reached a known terminal state: synced, partial, or skipped_known_reason.';

COMMENT ON COLUMN vkpi_sync_runs.traceback_text IS
'Application-truncated traceback: max 4KB and last 50 lines, whichever is shorter.';

COMMENT ON COLUMN vkpi_sync_runs.error_type IS
'Controlled error category: db_connection_lost, provider_timeout, data_field_missing, or other.';

CREATE INDEX IF NOT EXISTS idx_vkpi_sync_runs_status_started
    ON vkpi_sync_runs (status, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_sync_runs_stage_started
    ON vkpi_sync_runs (stage, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_sync_runs_error_type
    ON vkpi_sync_runs (error_type, started_at DESC)
    WHERE error_type IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_vkpi_sync_runs_interrupted_kol
    ON vkpi_sync_runs (interrupted_kol_pool_id)
    WHERE interrupted_kol_pool_id IS NOT NULL;
