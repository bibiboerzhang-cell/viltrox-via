-- Persist per-post official channel metrics so daily refresh can compute
-- true post-level deltas when account-level cumulative totals are protected.

CREATE TABLE IF NOT EXISTS vkpi_channel_post_metrics (
    id BIGSERIAL PRIMARY KEY,
    channel_id BIGINT NOT NULL REFERENCES vkpi_employee_channels(id) ON DELETE CASCADE,
    snapshot_date DATE NOT NULL,
    platform TEXT NOT NULL DEFAULT '',
    post_uid TEXT NOT NULL,
    post_url TEXT DEFAULT '',
    title TEXT DEFAULT '',
    posted_at TIMESTAMPTZ,
    views BIGINT NOT NULL DEFAULT 0,
    likes BIGINT NOT NULL DEFAULT 0,
    comments BIGINT NOT NULL DEFAULT 0,
    shares BIGINT NOT NULL DEFAULT 0,
    views_delta BIGINT NOT NULL DEFAULT 0,
    likes_delta BIGINT NOT NULL DEFAULT 0,
    comments_delta BIGINT NOT NULL DEFAULT 0,
    shares_delta BIGINT NOT NULL DEFAULT 0,
    delta_method TEXT NOT NULL DEFAULT 'post_metric_delta_v1',
    raw_post_json TEXT NOT NULL DEFAULT '{}',
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(channel_id, post_uid, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_channel_post_metrics_latest
    ON vkpi_channel_post_metrics(channel_id, post_uid, snapshot_date DESC, captured_at DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_channel_post_metrics_channel_date
    ON vkpi_channel_post_metrics(channel_id, snapshot_date DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_channel_post_metrics_platform_date
    ON vkpi_channel_post_metrics(platform, snapshot_date DESC);
