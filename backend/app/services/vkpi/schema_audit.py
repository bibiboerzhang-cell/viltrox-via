"""SQLite guard for V-KPI audit logs."""
from __future__ import annotations

from app.db.connection import get_conn, is_postgres_runtime

_SCHEMA_READY = False


def ensure_vkpi_audit_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY or is_postgres_runtime():
        return
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS vkpi_sensitive_access_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id INTEGER,
            action_type TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            resource_id TEXT DEFAULT '',
            page_path TEXT DEFAULT '',
            ip TEXT DEFAULT '',
            user_agent TEXT DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_sensitive_logs_staff_time
            ON vkpi_sensitive_access_logs(staff_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_vkpi_sensitive_logs_action_time
            ON vkpi_sensitive_access_logs(action_type, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_vkpi_sensitive_logs_resource
            ON vkpi_sensitive_access_logs(resource_type, resource_id);

        CREATE TABLE IF NOT EXISTS vkpi_export_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id INTEGER,
            export_kind TEXT NOT NULL,
            export_target TEXT NOT NULL DEFAULT '',
            metric_keys_json TEXT NOT NULL DEFAULT '[]',
            filters_json TEXT NOT NULL DEFAULT '{}',
            row_count INTEGER NOT NULL DEFAULT 0,
            file_size_bytes INTEGER NOT NULL DEFAULT 0,
            file_sha256 TEXT DEFAULT '',
            download_url TEXT DEFAULT '',
            contains_pii INTEGER NOT NULL DEFAULT 0,
            contains_financial INTEGER NOT NULL DEFAULT 0,
            purpose TEXT DEFAULT '',
            ip TEXT DEFAULT '',
            user_agent TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_export_logs_staff_time
            ON vkpi_export_logs(staff_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_vkpi_export_logs_kind_time
            ON vkpi_export_logs(export_kind, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_vkpi_export_logs_target
            ON vkpi_export_logs(export_target, created_at DESC);

        CREATE TABLE IF NOT EXISTS vkpi_settings_change_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id INTEGER,
            change_type TEXT NOT NULL,
            setting_key TEXT NOT NULL,
            old_value_redacted TEXT DEFAULT '',
            new_value_redacted TEXT DEFAULT '',
            full_value_hash TEXT DEFAULT '',
            ip TEXT DEFAULT '',
            user_agent TEXT DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            changed_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_settings_logs_staff_time
            ON vkpi_settings_change_logs(staff_id, changed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_vkpi_settings_logs_type_time
            ON vkpi_settings_change_logs(change_type, changed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_vkpi_settings_logs_key
            ON vkpi_settings_change_logs(setting_key, changed_at DESC);

        CREATE TABLE IF NOT EXISTS vkpi_business_audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id INTEGER,
            action_type TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT DEFAULT '',
            detail TEXT DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_business_audit_staff_time
            ON vkpi_business_audit_logs(staff_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_vkpi_business_audit_target
            ON vkpi_business_audit_logs(target_type, target_id, created_at DESC);
        """
    )
    conn.commit()
    _SCHEMA_READY = True
