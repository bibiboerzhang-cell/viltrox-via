-- P0 workflow execution fencing.
--
-- The original workflow tables recorded progress but had no exclusive claim,
-- lease expiry, fencing token or compare-and-swap version.  Two workers could
-- therefore execute the same step and both persist a checkpoint.  This
-- migration adds the durable execution identity used by workflow_repository
-- and makes one logical step/checkpoint unique per run.
--
-- The migration runner owns the transaction and fleet advisory lock.  Do not
-- add BEGIN/COMMIT here.  Existing duplicate/orphan rows intentionally make
-- this migration fail instead of being silently deleted.

ALTER TABLE vkpi_workflow_runs
    ADD COLUMN IF NOT EXISTS lease_owner TEXT,
    ADD COLUMN IF NOT EXISTS lease_token_hash TEXT,
    ADD COLUMN IF NOT EXISTS fence_token BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS attempt_no INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS row_version BIGINT NOT NULL DEFAULT 0;

ALTER TABLE vkpi_workflow_runs
    ADD CONSTRAINT ck_vkpi_workflow_run_fence_nonnegative
        CHECK (fence_token >= 0),
    ADD CONSTRAINT ck_vkpi_workflow_run_attempt_nonnegative
        CHECK (attempt_no >= 0),
    ADD CONSTRAINT ck_vkpi_workflow_run_version_nonnegative
        CHECK (row_version >= 0),
    ADD CONSTRAINT ck_vkpi_workflow_run_lease_identity
        CHECK (
            (lease_owner IS NULL AND lease_token_hash IS NULL AND lease_expires_at IS NULL)
            OR
            (
                lease_owner IS NOT NULL AND lease_owner <> ''
                AND lease_token_hash ~ '^[0-9a-f]{64}$'
                AND lease_expires_at IS NOT NULL
            )
        );

CREATE INDEX IF NOT EXISTS idx_vkpi_workflow_run_recovery
    ON vkpi_workflow_runs (status, lease_expires_at, id)
    WHERE status IN ('running', 'failed', 'paused');

ALTER TABLE vkpi_workflow_steps
    ADD COLUMN IF NOT EXISTS fence_token BIGINT NOT NULL DEFAULT 0;

ALTER TABLE vkpi_workflow_steps
    ADD CONSTRAINT fk_vkpi_workflow_step_run
        FOREIGN KEY (run_id) REFERENCES vkpi_workflow_runs(id) ON DELETE CASCADE,
    ADD CONSTRAINT ck_vkpi_workflow_step_fence_nonnegative
        CHECK (fence_token >= 0);

CREATE UNIQUE INDEX IF NOT EXISTS uq_vkpi_workflow_step_once
    ON vkpi_workflow_steps (run_id, step_index);

ALTER TABLE vkpi_workflow_checkpoints
    ADD COLUMN IF NOT EXISTS fence_token BIGINT NOT NULL DEFAULT 0;

ALTER TABLE vkpi_workflow_checkpoints
    ADD CONSTRAINT fk_vkpi_workflow_checkpoint_run
        FOREIGN KEY (run_id) REFERENCES vkpi_workflow_runs(id) ON DELETE CASCADE,
    ADD CONSTRAINT ck_vkpi_workflow_checkpoint_fence_nonnegative
        CHECK (fence_token >= 0);

CREATE UNIQUE INDEX IF NOT EXISTS uq_vkpi_workflow_checkpoint_once
    ON vkpi_workflow_checkpoints (run_id, step_index);
