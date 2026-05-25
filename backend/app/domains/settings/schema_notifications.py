"""SQLite guard for V-KPI notification settings.

Notification settings are intentionally storage-only in v3. Delivery workers
and outbound integrations stay disabled until the v4 notification layer exists.
"""
from __future__ import annotations

from app.db.connection import get_conn, is_postgres_runtime


def ensure_vkpi_notification_settings_schema() -> None:
    if is_postgres_runtime():
        return
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vkpi_notification_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id INTEGER NOT NULL UNIQUE,
            email_enabled INTEGER NOT NULL DEFAULT 0,
            in_app_enabled INTEGER NOT NULL DEFAULT 1,
            slack_enabled INTEGER NOT NULL DEFAULT 0,
            wechat_enabled INTEGER NOT NULL DEFAULT 0,
            daily_digest_enabled INTEGER NOT NULL DEFAULT 1,
            weekly_summary_enabled INTEGER NOT NULL DEFAULT 1,
            stalled_project_enabled INTEGER NOT NULL DEFAULT 1,
            claim_activity_enabled INTEGER NOT NULL DEFAULT 1,
            attribution_alert_enabled INTEGER NOT NULL DEFAULT 1,
            cost_alert_enabled INTEGER NOT NULL DEFAULT 0,
            system_alert_enabled INTEGER NOT NULL DEFAULT 1,
            quiet_hours_start TEXT NOT NULL DEFAULT '22:00',
            quiet_hours_end TEXT NOT NULL DEFAULT '08:00',
            timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
            preferences_json TEXT NOT NULL DEFAULT '{}',
            updated_by_staff_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_vkpi_notification_settings_staff
            ON vkpi_notification_settings(staff_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_vkpi_notification_settings_updated
            ON vkpi_notification_settings(updated_at DESC)
        """
    )
    conn.commit()
