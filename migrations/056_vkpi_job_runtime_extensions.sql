ALTER TABLE job_execution_ledger
  ADD COLUMN IF NOT EXISTS priority INTEGER NOT NULL DEFAULT 5,
  ADD COLUMN IF NOT EXISTS lock_key TEXT DEFAULT '',
  ADD COLUMN IF NOT EXISTS timeout_seconds INTEGER NOT NULL DEFAULT 300,
  ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS cancel_requested_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS estimated_cost NUMERIC(10,4),
  ADD COLUMN IF NOT EXISTS actual_cost NUMERIC(10,4);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_job_active_lock
  ON job_execution_ledger(lock_key)
  WHERE lock_key <> ''
    AND status IN ('queued', 'retrying', 'processing', 'running');

CREATE TABLE IF NOT EXISTS vkpi_async_task_items (
  id BIGSERIAL PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES job_execution_ledger(task_id) ON DELETE CASCADE,
  item_key TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempt INTEGER NOT NULL DEFAULT 0,
  result_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  error TEXT DEFAULT '',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(task_id, item_key)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_task_items_task
  ON vkpi_async_task_items(task_id, status);
