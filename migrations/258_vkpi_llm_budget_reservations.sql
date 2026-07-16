-- Release P0: every strict production LLM request reserves budget atomically
-- before provider network I/O, then settles the same reservation exactly once.
--
-- The migration runner owns the transaction and fleet advisory lock.  Do not
-- add BEGIN/COMMIT here.

CREATE TABLE IF NOT EXISTS vkpi_llm_budget_reservations (
    reservation_key TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model_name TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT '',
    request_hash TEXT NOT NULL,
    provider_scope TEXT NOT NULL,
    cost_scope TEXT NOT NULL DEFAULT '',
    cumulative_scopes_json TEXT NOT NULL DEFAULT '[]',
    estimated_cost_usd NUMERIC(18, 6) NOT NULL,
    actual_cost_usd NUMERIC(18, 6),
    state TEXT NOT NULL DEFAULT 'reserved',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    reserved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    provider_started_at TIMESTAMPTZ,
    settled_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_vkpi_llm_reservation_state CHECK (
        state IN ('reserved', 'provider_started', 'unknown', 'settled', 'released', 'blocked')
    ),
    CONSTRAINT ck_vkpi_llm_reservation_estimate CHECK (estimated_cost_usd > 0)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_llm_reservation_open
    ON vkpi_llm_budget_reservations (state, reserved_at)
    WHERE state IN ('reserved', 'provider_started', 'unknown');

CREATE INDEX IF NOT EXISTS idx_vkpi_llm_reservation_provider_open
    ON vkpi_llm_budget_reservations (provider_scope, state, reserved_at);

CREATE INDEX IF NOT EXISTS idx_vkpi_llm_reservation_cost_scope_open
    ON vkpi_llm_budget_reservations (cost_scope, state, reserved_at);
