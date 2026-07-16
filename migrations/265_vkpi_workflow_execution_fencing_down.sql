-- Roll back P0 workflow execution fencing.
-- The migration runner owns the transaction.

DROP INDEX IF EXISTS uq_vkpi_workflow_checkpoint_once;
ALTER TABLE vkpi_workflow_checkpoints
    DROP CONSTRAINT IF EXISTS ck_vkpi_workflow_checkpoint_fence_nonnegative,
    DROP CONSTRAINT IF EXISTS fk_vkpi_workflow_checkpoint_run,
    DROP COLUMN IF EXISTS fence_token;

DROP INDEX IF EXISTS uq_vkpi_workflow_step_once;
ALTER TABLE vkpi_workflow_steps
    DROP CONSTRAINT IF EXISTS ck_vkpi_workflow_step_fence_nonnegative,
    DROP CONSTRAINT IF EXISTS fk_vkpi_workflow_step_run,
    DROP COLUMN IF EXISTS fence_token;

DROP INDEX IF EXISTS idx_vkpi_workflow_run_recovery;
ALTER TABLE vkpi_workflow_runs
    DROP CONSTRAINT IF EXISTS ck_vkpi_workflow_run_lease_identity,
    DROP CONSTRAINT IF EXISTS ck_vkpi_workflow_run_version_nonnegative,
    DROP CONSTRAINT IF EXISTS ck_vkpi_workflow_run_attempt_nonnegative,
    DROP CONSTRAINT IF EXISTS ck_vkpi_workflow_run_fence_nonnegative,
    DROP COLUMN IF EXISTS row_version,
    DROP COLUMN IF EXISTS attempt_no,
    DROP COLUMN IF EXISTS heartbeat_at,
    DROP COLUMN IF EXISTS lease_expires_at,
    DROP COLUMN IF EXISTS fence_token,
    DROP COLUMN IF EXISTS lease_token_hash,
    DROP COLUMN IF EXISTS lease_owner;

DELETE FROM schema_migrations
WHERE version_key = '265_vkpi_workflow_execution_fencing.sql';
