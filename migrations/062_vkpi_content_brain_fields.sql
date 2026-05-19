-- V-KPI P6 content brain fields.
-- Keep analysis output at post/media level. Do not write content labels to
-- account snapshot rows.

ALTER TABLE vkpi_industry_posts
    ADD COLUMN IF NOT EXISTS content_tags_json TEXT NOT NULL DEFAULT '[]';

ALTER TABLE vkpi_industry_posts
    ADD COLUMN IF NOT EXISTS product_intents_json TEXT NOT NULL DEFAULT '[]';

ALTER TABLE vkpi_industry_posts
    ADD COLUMN IF NOT EXISTS risk_flags_json TEXT NOT NULL DEFAULT '[]';

ALTER TABLE vkpi_industry_posts
    ADD COLUMN IF NOT EXISTS brand_mentions_json TEXT NOT NULL DEFAULT '[]';

ALTER TABLE vkpi_industry_posts
    ADD COLUMN IF NOT EXISTS ai_summary TEXT DEFAULT '';

ALTER TABLE vkpi_industry_posts
    ADD COLUMN IF NOT EXISTS analyzed_at TIMESTAMPTZ;

ALTER TABLE vkpi_industry_posts
    ADD COLUMN IF NOT EXISTS analysis_version TEXT DEFAULT '';

ALTER TABLE vkpi_industry_posts
    ADD COLUMN IF NOT EXISTS analysis_status TEXT NOT NULL DEFAULT 'pending';

ALTER TABLE vkpi_industry_posts
    ADD COLUMN IF NOT EXISTS analysis_error TEXT DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_vkpi_industry_posts_analysis_status
    ON vkpi_industry_posts (analysis_status, analyzed_at);

CREATE TABLE IF NOT EXISTS vkpi_industry_post_media (
    id BIGSERIAL PRIMARY KEY,
    media_uid TEXT NOT NULL UNIQUE,
    post_id BIGINT NOT NULL REFERENCES vkpi_industry_posts(id) ON DELETE CASCADE,
    media_url TEXT DEFAULT '',
    thumbnail_url TEXT DEFAULT '',
    media_type TEXT DEFAULT '',
    duration_seconds INTEGER,
    source_platform TEXT DEFAULT '',
    source_json TEXT NOT NULL DEFAULT '{}',
    content_tags_json TEXT NOT NULL DEFAULT '[]',
    product_intents_json TEXT NOT NULL DEFAULT '[]',
    risk_flags_json TEXT NOT NULL DEFAULT '[]',
    brand_mentions_json TEXT NOT NULL DEFAULT '[]',
    ai_summary TEXT DEFAULT '',
    analyzed_at TIMESTAMPTZ,
    analysis_version TEXT DEFAULT '',
    analysis_status TEXT NOT NULL DEFAULT 'pending',
    analysis_error TEXT DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vkpi_industry_post_media_post
    ON vkpi_industry_post_media (post_id);

CREATE INDEX IF NOT EXISTS idx_vkpi_industry_post_media_analysis_status
    ON vkpi_industry_post_media (analysis_status, analyzed_at);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_vkpi_industry_post_media_url
    ON vkpi_industry_post_media (post_id, media_url)
    WHERE media_url IS NOT NULL AND media_url <> '';
