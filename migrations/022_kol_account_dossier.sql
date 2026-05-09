-- KOL account-level intelligence dossier.
-- Stores real Apify account scans, normalized posts/comments, and AI account reports.

CREATE TABLE IF NOT EXISTS kol_account_snapshots (
    id BIGSERIAL PRIMARY KEY,
    kol_id BIGINT NOT NULL REFERENCES kols(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    handle TEXT DEFAULT '',
    account_url TEXT DEFAULT '',
    follower_count INTEGER DEFAULT 0,
    content_count INTEGER DEFAULT 0,
    total_views BIGINT DEFAULT 0,
    total_likes BIGINT DEFAULT 0,
    total_comments BIGINT DEFAULT 0,
    total_shares BIGINT DEFAULT 0,
    avg_views INTEGER DEFAULT 0,
    engagement_rate DOUBLE PRECISION DEFAULT 0,
    brand_mentions_json TEXT NOT NULL DEFAULT '[]',
    competitor_mentions_json TEXT NOT NULL DEFAULT '[]',
    comment_count INTEGER DEFAULT 0,
    scan_status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT DEFAULT '',
    raw_json TEXT NOT NULL DEFAULT '{}',
    scanned_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_kol_account_snapshots_kol_time ON kol_account_snapshots(kol_id, scanned_at DESC);
CREATE INDEX IF NOT EXISTS idx_kol_account_snapshots_status ON kol_account_snapshots(scan_status);

CREATE TABLE IF NOT EXISTS kol_posts (
    id BIGSERIAL PRIMARY KEY,
    kol_id BIGINT NOT NULL REFERENCES kols(id) ON DELETE CASCADE,
    snapshot_id BIGINT REFERENCES kol_account_snapshots(id) ON DELETE SET NULL,
    platform TEXT NOT NULL,
    post_url TEXT NOT NULL,
    title TEXT DEFAULT '',
    thumbnail_url TEXT DEFAULT '',
    published_at TIMESTAMPTZ,
    content_type TEXT DEFAULT '',
    views INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    shares INTEGER DEFAULT 0,
    brand_mentions_json TEXT NOT NULL DEFAULT '[]',
    competitor_mentions_json TEXT NOT NULL DEFAULT '[]',
    comment_sentiment TEXT DEFAULT 'unknown',
    raw_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(kol_id, post_url)
);
CREATE INDEX IF NOT EXISTS idx_kol_posts_kol_views ON kol_posts(kol_id, views DESC);
CREATE INDEX IF NOT EXISTS idx_kol_posts_url ON kol_posts(post_url);

CREATE TABLE IF NOT EXISTS kol_comments (
    id BIGSERIAL PRIMARY KEY,
    kol_id BIGINT NOT NULL REFERENCES kols(id) ON DELETE CASCADE,
    post_id BIGINT REFERENCES kol_posts(id) ON DELETE SET NULL,
    platform TEXT NOT NULL,
    post_url TEXT DEFAULT '',
    author_handle TEXT DEFAULT '',
    comment_text TEXT NOT NULL,
    like_count INTEGER DEFAULT 0,
    sentiment TEXT DEFAULT 'unknown',
    intent_tags_json TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_kol_comments_kol_time ON kol_comments(kol_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_kol_comments_post ON kol_comments(post_id);

CREATE TABLE IF NOT EXISTS kol_analysis_reports (
    id BIGSERIAL PRIMARY KEY,
    kol_id BIGINT NOT NULL REFERENCES kols(id) ON DELETE CASCADE,
    snapshot_id BIGINT REFERENCES kol_account_snapshots(id) ON DELETE SET NULL,
    report_type TEXT NOT NULL DEFAULT 'account_dossier',
    account_score INTEGER DEFAULT 0,
    audience_fit INTEGER DEFAULT 0,
    product_fit INTEGER DEFAULT 0,
    risk_level TEXT DEFAULT 'unknown',
    recommended_action TEXT DEFAULT '',
    suggested_products_json TEXT NOT NULL DEFAULT '[]',
    brand_history_json TEXT NOT NULL DEFAULT '{}',
    comment_insights_json TEXT NOT NULL DEFAULT '{}',
    summary_zh TEXT DEFAULT '',
    summary_en TEXT DEFAULT '',
    raw_json TEXT NOT NULL DEFAULT '{}',
    method TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_kol_analysis_reports_kol_time ON kol_analysis_reports(kol_id, created_at DESC);
