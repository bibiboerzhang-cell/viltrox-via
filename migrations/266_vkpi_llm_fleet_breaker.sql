-- Fleet-wide LLM circuit breaker keyed by the exact provider/model binding.
--
-- The migration runner owns the transaction and advisory lock.  Do not add
-- BEGIN/COMMIT here.  The half-open owner/fence/lease tuple makes a probe a
-- durable, single-process capability: an expired or superseded probe cannot
-- close a newer breaker generation.

CREATE TABLE IF NOT EXISTS vkpi_llm_fleet_breakers (
    provider TEXT NOT NULL,
    model_name TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'closed',
    failure_streak INTEGER NOT NULL DEFAULT 0,
    opened_until TIMESTAMPTZ,
    generation BIGINT NOT NULL DEFAULT 1,
    version BIGINT NOT NULL DEFAULT 1,
    half_open_owner TEXT,
    half_open_fence BIGINT NOT NULL DEFAULT 0,
    half_open_lease_expires_at TIMESTAMPTZ,
    last_failure_class TEXT,
    last_failure_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (provider, model_name),
    CONSTRAINT ck_vkpi_llm_fleet_breaker_state CHECK (
        state IN ('closed', 'open', 'half_open')
    ),
    CONSTRAINT ck_vkpi_llm_fleet_breaker_failure_streak CHECK (failure_streak >= 0),
    CONSTRAINT ck_vkpi_llm_fleet_breaker_generation CHECK (generation > 0),
    CONSTRAINT ck_vkpi_llm_fleet_breaker_version CHECK (version > 0),
    CONSTRAINT ck_vkpi_llm_fleet_breaker_fence CHECK (half_open_fence >= 0),
    CONSTRAINT ck_vkpi_llm_fleet_breaker_half_open_lease CHECK (
        (state = 'half_open'
            AND half_open_owner IS NOT NULL
            AND half_open_lease_expires_at IS NOT NULL
            AND half_open_fence > 0)
        OR
        (state <> 'half_open'
            AND half_open_owner IS NULL
            AND half_open_lease_expires_at IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_vkpi_llm_fleet_breaker_opened_until
    ON vkpi_llm_fleet_breakers (state, opened_until);

CREATE INDEX IF NOT EXISTS idx_vkpi_llm_fleet_breaker_half_open_lease
    ON vkpi_llm_fleet_breakers (state, half_open_lease_expires_at)
    WHERE state = 'half_open';
