CREATE TABLE IF NOT EXISTS vkpi_media_cache_assets (
    id BIGSERIAL PRIMARY KEY,
    asset_uid TEXT NOT NULL UNIQUE,
    media_kind TEXT NOT NULL DEFAULT 'video',
    platform TEXT NOT NULL DEFAULT '',
    external_id TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    source_url_hash TEXT NOT NULL DEFAULT '',
    digest TEXT NOT NULL DEFAULT '',
    checksum TEXT NOT NULL DEFAULT '',
    content_type TEXT NOT NULL DEFAULT '',
    size_bytes BIGINT NOT NULL DEFAULT 0,
    storage_backend TEXT NOT NULL DEFAULT 'local',
    local_path TEXT NOT NULL DEFAULT '',
    r2_key TEXT NOT NULL DEFAULT '',
    cache_url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'cached',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vkpi_media_cache_assets_digest
    ON vkpi_media_cache_assets (media_kind, digest);

CREATE INDEX IF NOT EXISTS idx_vkpi_media_cache_assets_external
    ON vkpi_media_cache_assets (platform, external_id);

CREATE INDEX IF NOT EXISTS idx_vkpi_media_cache_assets_r2
    ON vkpi_media_cache_assets (storage_backend, r2_key);

CREATE INDEX IF NOT EXISTS idx_vkpi_media_cache_assets_source_hash
    ON vkpi_media_cache_assets (source_url_hash);
