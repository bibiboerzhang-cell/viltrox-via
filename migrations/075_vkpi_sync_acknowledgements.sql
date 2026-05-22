CREATE TABLE IF NOT EXISTS vkpi_sync_acknowledgements (
    ack_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL DEFAULT 'daily_incremental_sync',
    target_run_id TEXT,
    reason TEXT NOT NULL,
    acknowledged_by TEXT NOT NULL DEFAULT 'cli',
    acknowledged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE vkpi_sync_acknowledgements IS
'Manual acknowledgement ledger for sync guard blocks. Each ack must carry a reason.';

COMMENT ON COLUMN vkpi_sync_acknowledgements.scope IS
'Guard scope, currently daily_incremental_sync.';

COMMENT ON COLUMN vkpi_sync_acknowledgements.target_run_id IS
'Optional run_id that the acknowledgement explicitly clears.';

CREATE INDEX IF NOT EXISTS idx_vkpi_sync_ack_scope_time
    ON vkpi_sync_acknowledgements (scope, acknowledged_at DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_sync_ack_target_run
    ON vkpi_sync_acknowledgements (target_run_id, acknowledged_at DESC)
    WHERE target_run_id IS NOT NULL;
