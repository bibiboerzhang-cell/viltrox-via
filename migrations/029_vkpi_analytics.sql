-- V-KPI Analytics tables (第三个界面)
-- Wraps existing services/intelligence/lens_compare + lens_monitor +
-- adds outreach suggestions queue
-- Depends on: 023_vkpi_core.sql

-- ===========================================================================
-- vkpi_monitored_products
-- 配置要监控的产品列表（每天 cron 跑 lens_monitor）
-- ===========================================================================
CREATE TABLE IF NOT EXISTS vkpi_monitored_products (
    id BIGSERIAL PRIMARY KEY,
    product_sku TEXT NOT NULL UNIQUE,
    product_name TEXT NOT NULL DEFAULT '',
    series TEXT DEFAULT '',                     -- 'LAB' | 'Pro' | 'EVO Air' | ...
    mount TEXT DEFAULT '',                      -- 'E' | 'RF' | 'L' | 'Z' | 'X' | ...
    monitor_platforms_json TEXT NOT NULL DEFAULT '["youtube","instagram","tiktok","xiaohongshu"]',
    keywords_json TEXT NOT NULL DEFAULT '[]',   -- additional search keywords
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    last_monitored_at TIMESTAMPTZ,
    last_run_id BIGINT,
    created_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_monitored_products_enabled
    ON vkpi_monitored_products(enabled, last_monitored_at);


-- ===========================================================================
-- vkpi_analytics_runs
-- 每次 compare/monitor 调用的元数据（记录 AI 成本 + 触发人）
-- ===========================================================================
CREATE TABLE IF NOT EXISTS vkpi_analytics_runs (
    id BIGSERIAL PRIMARY KEY,
    run_uid TEXT NOT NULL UNIQUE,
    run_type TEXT NOT NULL,                     -- 'compare' | 'monitor' | 'trending'
    triggered_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running',     -- running | success | failed
    target_skus_json TEXT NOT NULL DEFAULT '[]',
    platforms_json TEXT NOT NULL DEFAULT '[]',
    period_start TIMESTAMPTZ,
    period_end TIMESTAMPTZ,
    summary_json TEXT NOT NULL DEFAULT '{}',    -- compact summary for UI
    raw_result_json TEXT NOT NULL DEFAULT '{}', -- full output (incl. AI insights)
    cost_usd_cents INTEGER NOT NULL DEFAULT 0,  -- AI + Apify cost in cents
    error_message TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_vkpi_analytics_runs_type_time
    ON vkpi_analytics_runs(run_type, triggered_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_analytics_runs_staff
    ON vkpi_analytics_runs(triggered_by_staff_id, triggered_at DESC);


-- ===========================================================================
-- vkpi_outreach_suggestions
-- 建议联系队列。lens_monitor 发现新 KOL 后写入这里。
-- 与 vkpi_kol_claims 联动：worked_before=true → "复联" 候选
-- ===========================================================================
CREATE TABLE IF NOT EXISTS vkpi_outreach_suggestions (
    id BIGSERIAL PRIMARY KEY,
    suggestion_uid TEXT NOT NULL UNIQUE,
    source_run_id BIGINT REFERENCES vkpi_analytics_runs(id) ON DELETE SET NULL,
    source_product_sku TEXT NOT NULL DEFAULT '',
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- KOL identity (may not yet exist in kols table)
    platform TEXT NOT NULL,                     -- 'youtube' | 'instagram' | 'tiktok' | 'xiaohongshu'
    handle TEXT NOT NULL,
    channel_name TEXT DEFAULT '',
    follower_count INTEGER,
    engagement_rate NUMERIC(6,4),
    country_code TEXT DEFAULT '',
    avatar_url TEXT DEFAULT '',
    profile_url TEXT DEFAULT '',

    -- Source content reference
    source_video_url TEXT DEFAULT '',
    source_video_title TEXT DEFAULT '',
    source_view_count INTEGER DEFAULT 0,
    source_like_count INTEGER DEFAULT 0,
    source_published_at TIMESTAMPTZ,

    -- Linkage
    existing_kol_id BIGINT REFERENCES kols(id) ON DELETE SET NULL,
    worked_before BOOLEAN NOT NULL DEFAULT FALSE,
    last_collab_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    last_collab_at TIMESTAMPTZ,

    -- Ranking signals
    mention_count INTEGER NOT NULL DEFAULT 1,   -- how many times mentioned this product
    is_viral BOOLEAN NOT NULL DEFAULT FALSE,    -- > some threshold
    priority INTEGER NOT NULL DEFAULT 0,        -- 0 normal | 5 high | 10 critical
    score NUMERIC(8,2) NOT NULL DEFAULT 0,      -- composite score (engagement × follower × viral)

    -- Lifecycle
    status TEXT NOT NULL DEFAULT 'new',         -- new | claimed | dismissed | expired
    claimed_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    claimed_at TIMESTAMPTZ,
    dismissed_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    dismissed_at TIMESTAMPTZ,
    dismissed_reason TEXT DEFAULT '',

    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (platform, handle, source_product_sku)
);
CREATE INDEX IF NOT EXISTS idx_vkpi_suggest_status_score
    ON vkpi_outreach_suggestions(status, priority DESC, score DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_suggest_product
    ON vkpi_outreach_suggestions(source_product_sku, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_suggest_worked_before
    ON vkpi_outreach_suggestions(worked_before, status);


-- ===========================================================================
-- 视图：建议联系总览
-- ===========================================================================
CREATE OR REPLACE VIEW vkpi_suggestions_overview AS
SELECT
    COUNT(*) FILTER (WHERE status = 'new') AS new_count,
    COUNT(*) FILTER (WHERE status = 'new' AND worked_before = TRUE) AS reactivation_count,
    COUNT(*) FILTER (WHERE status = 'new' AND worked_before = FALSE) AS fresh_count,
    COUNT(*) FILTER (WHERE status = 'new' AND priority >= 5) AS high_priority_count,
    COUNT(*) FILTER (WHERE status = 'new' AND is_viral = TRUE) AS viral_count,
    COUNT(*) FILTER (WHERE status = 'claimed') AS claimed_count,
    COUNT(*) FILTER (WHERE status = 'dismissed') AS dismissed_count
FROM vkpi_outreach_suggestions;
