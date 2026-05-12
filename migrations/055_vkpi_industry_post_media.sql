ALTER TABLE vkpi_industry_posts
    ADD COLUMN IF NOT EXISTS video_url TEXT DEFAULT '';

ALTER TABLE vkpi_industry_posts
    ADD COLUMN IF NOT EXISTS media_type TEXT DEFAULT '';

ALTER TABLE vkpi_industry_posts
    ADD COLUMN IF NOT EXISTS duration_seconds INTEGER;

ALTER TABLE vkpi_industry_posts
    ADD COLUMN IF NOT EXISTS video_source TEXT DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_vkpi_industry_posts_media_type
    ON vkpi_industry_posts(media_type);
