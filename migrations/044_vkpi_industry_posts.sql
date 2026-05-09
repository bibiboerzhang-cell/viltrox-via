-- V-KPI Industry Data post-level facts.
CREATE TABLE IF NOT EXISTS vkpi_industry_posts (
    id BIGSERIAL PRIMARY KEY,
    post_uid TEXT NOT NULL UNIQUE,
    account_id BIGINT REFERENCES vkpi_industry_accounts(id) ON DELETE SET NULL,
    platform TEXT NOT NULL,
    platform_post_id TEXT DEFAULT '',
    post_url TEXT DEFAULT '',
    thumbnail_url TEXT DEFAULT '',
    title TEXT DEFAULT '',
    caption TEXT DEFAULT '',
    published_at TIMESTAMPTZ,
    views BIGINT,
    likes BIGINT,
    comments BIGINT,
    shares BIGINT,
    saves BIGINT,
    hashtags_json TEXT NOT NULL DEFAULT '[]',
    mentions_json TEXT NOT NULL DEFAULT '[]',
    detected_products_json TEXT NOT NULL DEFAULT '[]',
    content_pillar TEXT DEFAULT '',
    sentiment TEXT DEFAULT '',
    raw_platform_data TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS vkpi_industry_post_metrics (
    id BIGSERIAL PRIMARY KEY,
    post_id BIGINT NOT NULL REFERENCES vkpi_industry_posts(id) ON DELETE CASCADE,
    snapshot_date DATE NOT NULL,
    views BIGINT,
    likes BIGINT,
    comments BIGINT,
    shares BIGINT,
    saves BIGINT,
    raw_platform_data TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(post_id, snapshot_date)
);

CREATE TABLE IF NOT EXISTS vkpi_industry_post_tags (
    id BIGSERIAL PRIMARY KEY,
    post_id BIGINT NOT NULL REFERENCES vkpi_industry_posts(id) ON DELETE CASCADE,
    tag_type TEXT NOT NULL,
    tag_value TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    created_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS vkpi_industry_reports (
    id BIGSERIAL PRIMARY KEY,
    report_uid TEXT NOT NULL UNIQUE,
    project_id BIGINT REFERENCES vkpi_industry_projects(id) ON DELETE SET NULL,
    report_type TEXT NOT NULL DEFAULT 'industry',
    status TEXT NOT NULL DEFAULT 'queued',
    file_path TEXT DEFAULT '',
    summary_json TEXT NOT NULL DEFAULT '{}',
    created_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);
