DROP INDEX IF EXISTS uniq_vkpi_industry_post_media_url;
DROP INDEX IF EXISTS idx_vkpi_industry_post_media_analysis_status;
DROP INDEX IF EXISTS idx_vkpi_industry_post_media_post;
DROP TABLE IF EXISTS vkpi_industry_post_media;

DROP INDEX IF EXISTS idx_vkpi_industry_posts_analysis_status;

ALTER TABLE vkpi_industry_posts
    DROP COLUMN IF EXISTS analysis_error,
    DROP COLUMN IF EXISTS analysis_status,
    DROP COLUMN IF EXISTS analysis_version,
    DROP COLUMN IF EXISTS analyzed_at,
    DROP COLUMN IF EXISTS ai_summary,
    DROP COLUMN IF EXISTS brand_mentions_json,
    DROP COLUMN IF EXISTS risk_flags_json,
    DROP COLUMN IF EXISTS product_intents_json,
    DROP COLUMN IF EXISTS content_tags_json;
