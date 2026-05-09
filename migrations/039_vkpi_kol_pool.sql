-- V-KPI self-owned KOL pool.
CREATE TABLE IF NOT EXISTS vkpi_kol_pool (
    id BIGSERIAL PRIMARY KEY,
    pool_uid TEXT NOT NULL UNIQUE,
    platform TEXT NOT NULL,
    handle TEXT NOT NULL,
    profile_url TEXT DEFAULT '',
    display_name TEXT DEFAULT '',
    avatar_url TEXT DEFAULT '',
    bio TEXT DEFAULT '',
    country TEXT DEFAULT '',
    language TEXT DEFAULT '',
    email TEXT DEFAULT '',
    other_contacts_json TEXT NOT NULL DEFAULT '[]',
    followers BIGINT,
    following BIGINT,
    posts_count BIGINT,
    avg_views BIGINT,
    avg_likes BIGINT,
    avg_comments BIGINT,
    avg_shares BIGINT,
    engagement_rate NUMERIC(8,5),
    primary_topic TEXT DEFAULT '',
    secondary_topics_json TEXT NOT NULL DEFAULT '[]',
    content_style TEXT DEFAULT '',
    production_quality TEXT DEFAULT '',
    audience_estimated_json TEXT NOT NULL DEFAULT '{}',
    brand_collaborations_json TEXT NOT NULL DEFAULT '[]',
    viltrox_fit_score NUMERIC(8,3),
    viltrox_fit_reason TEXT DEFAULT '',
    potential_concerns_json TEXT NOT NULL DEFAULT '[]',
    recommended_product_lines_json TEXT NOT NULL DEFAULT '[]',
    linked_main_kol_id BIGINT REFERENCES kols(id) ON DELETE SET NULL,
    sync_status TEXT NOT NULL DEFAULT 'imported',
    source_type TEXT NOT NULL DEFAULT 'manual',
    source_ref TEXT DEFAULT '',
    raw_platform_data TEXT NOT NULL DEFAULT '{}',
    created_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    last_seen_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(platform, handle)
);
CREATE INDEX IF NOT EXISTS idx_vkpi_kol_pool_platform_handle ON vkpi_kol_pool(platform, handle);
CREATE INDEX IF NOT EXISTS idx_vkpi_kol_pool_score ON vkpi_kol_pool(viltrox_fit_score DESC);

CREATE TABLE IF NOT EXISTS vkpi_kol_pool_aliases (
    id BIGSERIAL PRIMARY KEY,
    kol_pool_id BIGINT NOT NULL REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    handle TEXT NOT NULL,
    profile_url TEXT DEFAULT '',
    confidence NUMERIC(8,5),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(platform, handle)
);

CREATE TABLE IF NOT EXISTS vkpi_kol_pool_brand_links (
    id BIGSERIAL PRIMARY KEY,
    kol_pool_id BIGINT NOT NULL REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
    brand TEXT NOT NULL,
    product_name TEXT DEFAULT '',
    evidence_url TEXT DEFAULT '',
    observed_at TIMESTAMPTZ,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS vkpi_kol_embeddings (
    id BIGSERIAL PRIMARY KEY,
    kol_pool_id BIGINT NOT NULL REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
    embedding_model TEXT NOT NULL DEFAULT '',
    embedding_vector_json TEXT NOT NULL DEFAULT '[]',
    embedding_text_hash TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'not_configured',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(kol_pool_id, embedding_model)
);

CREATE TABLE IF NOT EXISTS vkpi_content_pillars (
    id BIGSERIAL PRIMARY KEY,
    pillar_uid TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    keywords_json TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
