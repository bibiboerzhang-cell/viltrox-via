-- P1.X.A KOL refresh tier selector.
-- This table gates daily KOL refresh to a qualified subset. It must not be
-- used to re-enable legacy full-pool daily refresh.

CREATE TABLE IF NOT EXISTS vkpi_kol_refresh_tier (
    id BIGSERIAL PRIMARY KEY,
    kol_pool_id BIGINT NOT NULL UNIQUE REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,

    tier TEXT NOT NULL DEFAULT 'cold',
    tier_reason TEXT NOT NULL DEFAULT 'cold_default',
    tier_reason_json TEXT NOT NULL DEFAULT '{}',
    tier_assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    last_refresh_at TIMESTAMPTZ,
    last_refresh_status TEXT DEFAULT '',

    manual_hot_flag BOOLEAN NOT NULL DEFAULT FALSE,
    manual_hot_set_by BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    manual_hot_set_at TIMESTAMPTZ,
    manual_hot_reason TEXT DEFAULT '',

    search_count_30d INTEGER NOT NULL DEFAULT 0,
    last_searched_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_refresh_tier_tier
    ON vkpi_kol_refresh_tier(tier);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_refresh_tier_last_refresh
    ON vkpi_kol_refresh_tier(last_refresh_at);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_refresh_tier_manual_hot
    ON vkpi_kol_refresh_tier(manual_hot_flag)
    WHERE manual_hot_flag = TRUE;

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_refresh_tier_last_search
    ON vkpi_kol_refresh_tier(last_searched_at);
