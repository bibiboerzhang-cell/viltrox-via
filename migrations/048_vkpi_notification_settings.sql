CREATE TABLE IF NOT EXISTS vkpi_notification_settings (
    id BIGSERIAL PRIMARY KEY,
    staff_id BIGINT NOT NULL UNIQUE,
    email_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    in_app_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    slack_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    wechat_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    daily_digest_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    weekly_summary_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    stalled_project_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    claim_activity_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    attribution_alert_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    cost_alert_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    system_alert_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    quiet_hours_start TEXT NOT NULL DEFAULT '22:00',
    quiet_hours_end TEXT NOT NULL DEFAULT '08:00',
    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    preferences_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_by_staff_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vkpi_notification_settings_staff
    ON vkpi_notification_settings(staff_id);

CREATE INDEX IF NOT EXISTS idx_vkpi_notification_settings_updated
    ON vkpi_notification_settings(updated_at DESC);
