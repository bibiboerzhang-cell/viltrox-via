-- PostgreSQL fleet-wide APScheduler leadership and per-fire idempotency ledger.
-- The leader itself is a session-level advisory lock; this durable table is the
-- second line of defence if leadership changes around one scheduled minute.
-- The migration runner owns the surrounding transaction and advisory lock.
-- Do not add BEGIN/COMMIT here.

CREATE TABLE IF NOT EXISTS vkpi_scheduler_fire_claims (
    id                BIGSERIAL PRIMARY KEY,
    task_key          TEXT NOT NULL,
    scheduled_fire_at TIMESTAMPTZ NOT NULL,
    leader_id         TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'running'
                      CHECK (status IN ('running', 'completed', 'failed')),
    claimed_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at      TIMESTAMPTZ,
    error             TEXT NOT NULL DEFAULT '',
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (task_key, scheduled_fire_at)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_scheduler_fire_claims_recent
    ON vkpi_scheduler_fire_claims (scheduled_fire_at DESC, task_key);

COMMENT ON TABLE vkpi_scheduler_fire_claims IS
    'Fleet scheduler fire idempotency: one claim per task and exact planned UTC fire time.';
