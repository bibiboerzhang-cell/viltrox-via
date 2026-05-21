-- Create base vkpi_kol_profile_deep table.
-- This table holds the deep profile payload for KOL pool entries.
-- Designed to satisfy eleven_dimensions.py backfill function matching paths.

CREATE TABLE IF NOT EXISTS vkpi_kol_profile_deep (
    id BIGSERIAL PRIMARY KEY,
    kol_pool_id BIGINT NOT NULL,
    kol_entity_uid TEXT,
    platform TEXT,
    handle TEXT,
    dimensions_11_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT vkpi_kol_profile_deep_kol_pool_id_unique UNIQUE (kol_pool_id)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_profile_deep_entity_uid
    ON vkpi_kol_profile_deep (kol_entity_uid)
    WHERE kol_entity_uid IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_profile_deep_platform_handle
    ON vkpi_kol_profile_deep (LOWER(platform), LOWER(handle))
    WHERE platform IS NOT NULL AND handle IS NOT NULL;
