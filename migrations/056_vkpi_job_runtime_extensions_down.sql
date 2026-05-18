DROP INDEX IF EXISTS idx_vkpi_task_items_task;
DROP TABLE IF EXISTS vkpi_async_task_items;
DROP INDEX IF EXISTS uniq_job_active_lock;

ALTER TABLE job_execution_ledger
  DROP COLUMN IF EXISTS actual_cost,
  DROP COLUMN IF EXISTS estimated_cost,
  DROP COLUMN IF EXISTS cancel_requested_at,
  DROP COLUMN IF EXISTS heartbeat_at,
  DROP COLUMN IF EXISTS timeout_seconds,
  DROP COLUMN IF EXISTS lock_key,
  DROP COLUMN IF EXISTS priority;
