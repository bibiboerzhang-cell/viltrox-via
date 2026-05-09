-- V-KPI Employee Channels (第四个界面 - 简化版)
-- 员工绑定自己的平台账号 + 数据回流
-- 简版：手动填 API key（OAuth 完整版后续 P5）
-- Depends on: 023_vkpi_core.sql

-- ===========================================================================
-- vkpi_employee_channels
-- 一个员工可以绑定多个平台账号
-- ===========================================================================
CREATE TABLE IF NOT EXISTS vkpi_employee_channels (
    id BIGSERIAL PRIMARY KEY,
    channel_uid TEXT NOT NULL UNIQUE,
    staff_id BIGINT NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,                     -- 'youtube' | 'instagram' | 'tiktok' | 'xiaohongshu' | 'facebook' | 'twitter'
    account_handle TEXT NOT NULL,               -- @handle
    account_display_name TEXT DEFAULT '',
    account_url TEXT DEFAULT '',
    avatar_url TEXT DEFAULT '',

    -- 简版：API key 直接存（加密）
    -- 完整 OAuth 版本：refresh_token + access_token + token_expiry
    api_key_encrypted TEXT DEFAULT '',          -- AES-encrypted
    api_secret_encrypted TEXT DEFAULT '',
    refresh_token_encrypted TEXT DEFAULT '',
    access_token_encrypted TEXT DEFAULT '',
    token_expiry_at TIMESTAMPTZ,
    auth_method TEXT NOT NULL DEFAULT 'manual_api_key',  -- 'manual_api_key' | 'oauth'

    -- Self-reported metrics (手动填的，等 OAuth 后自动同步)
    self_reported_followers INTEGER DEFAULT 0,
    self_reported_posts INTEGER DEFAULT 0,

    -- Sync state
    status TEXT NOT NULL DEFAULT 'active',      -- active | paused | revoked | error
    last_sync_at TIMESTAMPTZ,
    last_sync_status TEXT DEFAULT '',
    last_sync_error TEXT DEFAULT '',
    sync_failure_count INTEGER NOT NULL DEFAULT 0,

    -- Lifecycle
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ,

    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (staff_id, platform, account_handle)
);
CREATE INDEX IF NOT EXISTS idx_vkpi_emp_channels_staff
    ON vkpi_employee_channels(staff_id, status);
CREATE INDEX IF NOT EXISTS idx_vkpi_emp_channels_platform
    ON vkpi_employee_channels(platform, status);
CREATE INDEX IF NOT EXISTS idx_vkpi_emp_channels_sync
    ON vkpi_employee_channels(status, last_sync_at)
    WHERE deleted_at IS NULL;


-- ===========================================================================
-- vkpi_channel_metrics
-- 每天每个 channel 一行 snapshot（time-series）
-- ===========================================================================
CREATE TABLE IF NOT EXISTS vkpi_channel_metrics (
    id BIGSERIAL PRIMARY KEY,
    channel_id BIGINT NOT NULL REFERENCES vkpi_employee_channels(id) ON DELETE CASCADE,
    snapshot_date DATE NOT NULL,

    followers INTEGER NOT NULL DEFAULT 0,
    following INTEGER NOT NULL DEFAULT 0,
    posts_count INTEGER NOT NULL DEFAULT 0,

    total_views BIGINT NOT NULL DEFAULT 0,      -- across all posts
    total_likes BIGINT NOT NULL DEFAULT 0,
    total_comments BIGINT NOT NULL DEFAULT 0,
    total_shares BIGINT NOT NULL DEFAULT 0,

    -- Period diffs (vs previous snapshot)
    followers_delta INTEGER NOT NULL DEFAULT 0,
    posts_delta INTEGER NOT NULL DEFAULT 0,
    views_delta_24h BIGINT NOT NULL DEFAULT 0,
    likes_delta_24h BIGINT NOT NULL DEFAULT 0,

    engagement_rate NUMERIC(6,4) DEFAULT 0,

    raw_payload_json TEXT NOT NULL DEFAULT '{}',
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (channel_id, snapshot_date)
);
CREATE INDEX IF NOT EXISTS idx_vkpi_channel_metrics_channel_date
    ON vkpi_channel_metrics(channel_id, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_channel_metrics_date
    ON vkpi_channel_metrics(snapshot_date DESC);


-- ===========================================================================
-- vkpi_channel_audit
-- 绑定 / 解绑 / sync 失败 / 数据导出等审计
-- ===========================================================================
CREATE TABLE IF NOT EXISTS vkpi_channel_audit (
    id BIGSERIAL PRIMARY KEY,
    channel_id BIGINT REFERENCES vkpi_employee_channels(id) ON DELETE SET NULL,
    staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    action TEXT NOT NULL,                       -- 'bind' | 'unbind' | 'sync_success' | 'sync_failed' | 'view_admin' | 'export'
    detail TEXT DEFAULT '',
    ip TEXT DEFAULT '',
    user_agent TEXT DEFAULT '',
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_channel_audit_channel
    ON vkpi_channel_audit(channel_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_channel_audit_staff
    ON vkpi_channel_audit(staff_id, occurred_at DESC);


-- ===========================================================================
-- 视图：员工平台总览（manager 视角）
-- ===========================================================================
CREATE OR REPLACE VIEW vkpi_team_channels_overview AS
SELECT
    c.staff_id,
    COALESCE(u.name, u.email) AS staff_name,
    COUNT(*) FILTER (WHERE c.status = 'active') AS active_channels,
    COUNT(*) FILTER (WHERE c.status = 'error') AS error_channels,
    COALESCE(SUM(latest.followers), 0) AS total_followers,
    COALESCE(SUM(latest.total_views), 0) AS total_views,
    MAX(c.last_sync_at) AS most_recent_sync_at
FROM vkpi_employee_channels c
LEFT JOIN staff s ON s.id = c.staff_id
LEFT JOIN users u ON u.id = s.user_id
LEFT JOIN LATERAL (
    SELECT followers, total_views
    FROM vkpi_channel_metrics
    WHERE channel_id = c.id
    ORDER BY snapshot_date DESC LIMIT 1
) latest ON TRUE
WHERE c.deleted_at IS NULL
GROUP BY c.staff_id, u.name, u.email
ORDER BY total_followers DESC;
