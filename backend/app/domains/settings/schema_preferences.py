"""SQLite guard for V-KPI user-scoped preferences."""
from __future__ import annotations

from app.db.connection import get_conn, is_postgres_runtime

_SCHEMA_READY = False


def ensure_vkpi_preferences_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY or is_postgres_runtime():
        return
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS vkpi_user_preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id INTEGER NOT NULL UNIQUE,
            locale TEXT NOT NULL DEFAULT 'zh-CN',
            timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
            date_range_default TEXT NOT NULL DEFAULT '7d',
            landing_page TEXT NOT NULL DEFAULT 'dashboard',
            dashboard_scope_default TEXT NOT NULL DEFAULT 'self',
            table_density TEXT NOT NULL DEFAULT 'comfortable',
            rows_per_page INTEGER NOT NULL DEFAULT 20,
            compact_mode INTEGER NOT NULL DEFAULT 0,
            right_panel_open INTEGER NOT NULL DEFAULT 1,
            preferences_json TEXT NOT NULL DEFAULT '{}',
            updated_by_staff_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_user_preferences_staff
            ON vkpi_user_preferences(staff_id);
        CREATE INDEX IF NOT EXISTS idx_vkpi_user_preferences_updated
            ON vkpi_user_preferences(updated_at DESC);
        """
    )
    conn.commit()
    _SCHEMA_READY = True
