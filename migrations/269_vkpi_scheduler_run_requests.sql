-- 269: Durable cross-process requests for the scheduler "run now" control.
--
-- The admin API and the fleet-leading scheduler are separate processes in the
-- deployed topology.  A request therefore enters this table first and is
-- dispatched only by a process that owns a running APScheduler instance.
-- Dispatch changes APScheduler's next_run_time; it never calls a task/provider
-- function directly.
--
-- The migration runner owns the surrounding transaction and advisory lock.
-- Do not add BEGIN/COMMIT here.

CREATE TABLE IF NOT EXISTS vkpi_scheduler_run_requests (
    id BIGSERIAL PRIMARY KEY,
    task_key TEXT NOT NULL,
    requested_by BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    claimed_at TIMESTAMPTZ,
    dispatched_at TIMESTAMPTZ,
    error TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_scheduler_run_request_task_key CHECK (
      task_key <> '' AND length(task_key) <= 160
    ),
    CONSTRAINT chk_scheduler_run_request_status CHECK (
      status IN ('queued', 'dispatched', 'error')
    )
);

-- A repeated click while the same task is still queued reuses the existing
-- request instead of generating an unbounded burst.
CREATE UNIQUE INDEX IF NOT EXISTS uq_scheduler_run_requests_queued_task
  ON vkpi_scheduler_run_requests(task_key)
  WHERE status = 'queued';

CREATE INDEX IF NOT EXISTS idx_scheduler_run_requests_dispatch
  ON vkpi_scheduler_run_requests(status, created_at ASC, id ASC);

COMMENT ON TABLE vkpi_scheduler_run_requests IS
  'Durable admin run-now requests; the scheduler leader dispatches them through APScheduler only.';
COMMENT ON COLUMN vkpi_scheduler_run_requests.claimed_at IS
  'Set inside the same short transaction that dispatches and terminalizes the request.';
