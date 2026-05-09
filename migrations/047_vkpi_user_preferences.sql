-- V-KPI per-staff user preferences.
CREATE TABLE IF NOT EXISTS vkpi_user_preferences (
    id BIGSERIAL PRIMARY KEY,
    staff_id BIGINT NOT NULL REFERENCES staff(id) ON DELETE CASCADE UNIQUE,
    locale TEXT NOT NULL DEFAULT 'zh-CN',
    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    date_range_default TEXT NOT NULL DEFAULT '7d',
    landing_page TEXT NOT NULL DEFAULT 'dashboard',
    dashboard_scope_default TEXT NOT NULL DEFAULT 'self',
    table_density TEXT NOT NULL DEFAULT 'comfortable',
    rows_per_page INTEGER NOT NULL DEFAULT 20,
    compact_mode BOOLEAN NOT NULL DEFAULT FALSE,
    right_panel_open BOOLEAN NOT NULL DEFAULT TRUE,
    preferences_json TEXT NOT NULL DEFAULT '{}',
    updated_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_user_preferences_staff
    ON vkpi_user_preferences(staff_id);
CREATE INDEX IF NOT EXISTS idx_vkpi_user_preferences_updated
    ON vkpi_user_preferences(updated_at DESC);
