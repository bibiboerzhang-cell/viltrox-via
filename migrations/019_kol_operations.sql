-- KOL Operations Phase A.

CREATE TABLE IF NOT EXISTS kols (
    id BIGSERIAL PRIMARY KEY,
    channel_name TEXT NOT NULL,
    channel_url TEXT,
    platform TEXT NOT NULL,
    country TEXT,
    niche TEXT,
    project_name TEXT,
    owner_name TEXT,
    media_name TEXT,
    duplicate_flag TEXT,
    scale_tier TEXT,
    content_type TEXT,
    approval_note TEXT,
    channel_tags TEXT,
    affiliate_id TEXT,
    affiliate_link TEXT,
    discount_code TEXT,
    amazon_link TEXT,
    short_link TEXT,
    primary_category TEXT,
    promoted_product TEXT,
    follower_count INTEGER DEFAULT 0,
    avg_views INTEGER DEFAULT 0,
    contact_email TEXT,
    contact_phone TEXT,
    contact_status TEXT DEFAULT 'cold',
    notes TEXT,
    assigned_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    created_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_kols_assigned ON kols(assigned_staff_id);
CREATE INDEX IF NOT EXISTS idx_kols_platform_country ON kols(platform, country);
CREATE INDEX IF NOT EXISTS idx_kols_status ON kols(contact_status);

CREATE TABLE IF NOT EXISTS kol_outreach (
    id BIGSERIAL PRIMARY KEY,
    kol_id BIGINT NOT NULL REFERENCES kols(id) ON DELETE CASCADE,
    staff_id BIGINT NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    action_type TEXT NOT NULL,
    action_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes TEXT,
    next_action_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_outreach_kol ON kol_outreach(kol_id);

CREATE TABLE IF NOT EXISTS kol_campaigns (
    id BIGSERIAL PRIMARY KEY,
    kol_id BIGINT NOT NULL REFERENCES kols(id) ON DELETE CASCADE,
    product_sku TEXT,
    staff_id BIGINT NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    cost_cents INTEGER DEFAULT 0,
    status TEXT DEFAULT 'planning',
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_campaigns_kol ON kol_campaigns(kol_id);
CREATE INDEX IF NOT EXISTS idx_campaigns_staff ON kol_campaigns(staff_id);

CREATE TABLE IF NOT EXISTS kol_content (
    id BIGSERIAL PRIMARY KEY,
    campaign_id BIGINT NOT NULL REFERENCES kol_campaigns(id) ON DELETE CASCADE,
    content_url TEXT NOT NULL,
    platform TEXT NOT NULL,
    posted_at TIMESTAMPTZ,
    views INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    shares INTEGER DEFAULT 0,
    engagement_rate REAL DEFAULT 0,
    ai_quality_score INTEGER,
    ai_summary TEXT,
    ai_topics_json TEXT,
    content_title TEXT DEFAULT '',
    thumbnail_url TEXT DEFAULT '',
    scraped_text TEXT DEFAULT '',
    visible_comments_json TEXT DEFAULT '[]',
    ai_analysis_json TEXT DEFAULT '{}',
    analysis_status TEXT DEFAULT 'not_analyzed',
    analysis_error TEXT DEFAULT '',
    analysis_method TEXT DEFAULT '',
    last_metric_refresh TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_content_campaign ON kol_content(campaign_id);

CREATE TABLE IF NOT EXISTS kol_attribution (
    id BIGSERIAL PRIMARY KEY,
    content_id BIGINT NOT NULL REFERENCES kol_content(id) ON DELETE CASCADE,
    shopify_order_id TEXT,
    attributed_revenue_cents INTEGER DEFAULT 0,
    attributed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(content_id, shopify_order_id)
);

CREATE TABLE IF NOT EXISTS kol_activity_log (
    id BIGSERIAL PRIMARY KEY,
    staff_id BIGINT,
    user_id BIGINT,
    staff_name TEXT,
    action_type TEXT NOT NULL,
    target_type TEXT,
    target_id BIGINT,
    query TEXT,
    platform TEXT,
    market TEXT,
    api_provider TEXT,
    api_calls INTEGER DEFAULT 0,
    result_count INTEGER DEFAULT 0,
    metadata_json TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_kol_activity_created ON kol_activity_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_kol_activity_staff ON kol_activity_log(staff_id, created_at DESC);
