-- Release P0: a Redis worker is release-ready only after it has proved Redis
-- connectivity/group/consumer readiness, and every paid Apify start is fenced
-- by a durable task claim plus an atomic budget reservation.
--
-- The migration runner owns the transaction and fleet advisory lock.  Do not
-- add BEGIN/COMMIT here.

ALTER TABLE vkpi_worker_heartbeat
    ADD COLUMN IF NOT EXISTS redis_ready BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS redis_readiness_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS redis_stream_key TEXT,
    ADD COLUMN IF NOT EXISTS redis_group_name TEXT,
    ADD COLUMN IF NOT EXISTS redis_consumer_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS redis_ready_sequence BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS redis_heartbeat_interval_seconds INTEGER NOT NULL DEFAULT 15,
    ADD COLUMN IF NOT EXISTS redis_readiness_error_code TEXT;

CREATE TABLE IF NOT EXISTS vkpi_provider_execution_claims (
    task_id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL DEFAULT '',
    lease_owner TEXT NOT NULL,
    fence_token BIGINT NOT NULL DEFAULT 1,
    state TEXT NOT NULL DEFAULT 'active',
    lease_expires_at TIMESTAMPTZ NOT NULL,
    provider_run_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    CONSTRAINT ck_vkpi_provider_execution_claim_state CHECK (
        state IN ('active', 'completed', 'failed', 'blocked', 'unknown')
    ),
    CONSTRAINT ck_vkpi_provider_execution_claim_fence CHECK (fence_token > 0)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_provider_execution_claim_lease
    ON vkpi_provider_execution_claims (state, lease_expires_at);

CREATE TABLE IF NOT EXISTS vkpi_apify_budget_reservations (
    reservation_key TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    execution_fence_token BIGINT NOT NULL,
    estimate_source TEXT NOT NULL,
    estimated_cost_usd NUMERIC(18, 6) NOT NULL,
    actual_cost_usd NUMERIC(18, 6),
    state TEXT NOT NULL DEFAULT 'reserved',
    apify_run_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    reserved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    provider_started_at TIMESTAMPTZ,
    settled_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_vkpi_apify_reservation_request
        UNIQUE (task_id, actor_id, operation, payload_hash),
    CONSTRAINT fk_vkpi_apify_reservation_task
        FOREIGN KEY (task_id) REFERENCES vkpi_provider_execution_claims(task_id),
    CONSTRAINT ck_vkpi_apify_reservation_state CHECK (
        state IN ('reserved', 'provider_started', 'unknown', 'settled', 'released', 'blocked')
    ),
    CONSTRAINT ck_vkpi_apify_reservation_estimate CHECK (estimated_cost_usd > 0),
    CONSTRAINT ck_vkpi_apify_reservation_fence CHECK (execution_fence_token > 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_vkpi_apify_reservation_run
    ON vkpi_apify_budget_reservations (apify_run_id)
    WHERE apify_run_id IS NOT NULL AND apify_run_id <> '';

CREATE INDEX IF NOT EXISTS idx_vkpi_apify_reservation_open
    ON vkpi_apify_budget_reservations (state, reserved_at);

CREATE INDEX IF NOT EXISTS idx_vkpi_apify_reservation_actor_history
    ON vkpi_apify_budget_reservations (actor_id, operation, settled_at DESC)
    WHERE state = 'settled' AND actual_cost_usd IS NOT NULL;
