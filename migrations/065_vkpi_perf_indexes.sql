-- V-KPI performance indexes for dashboard, channel matrix, and KOL pool reads.

CREATE INDEX IF NOT EXISTS idx_vkpi_channel_metrics_latest
    ON vkpi_channel_metrics(channel_id, snapshot_date DESC, captured_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_employee_channels_active_platform
    ON vkpi_employee_channels(platform, account_handle)
    WHERE deleted_at IS NULL AND status = 'active';

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_pool_source_created
    ON vkpi_kol_pool(source_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_pool_platform_score
    ON vkpi_kol_pool(platform, viltrox_fit_score DESC, followers DESC);

ANALYZE vkpi_channel_metrics;
ANALYZE vkpi_employee_channels;
ANALYZE vkpi_kol_pool;
