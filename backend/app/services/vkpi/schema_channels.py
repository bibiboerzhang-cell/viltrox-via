"""SQLite guard for V-KPI employee channels."""
from __future__ import annotations

from app.db.connection import get_conn, is_postgres_runtime

_SCHEMA_READY = False


def ensure_vkpi_channels_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY or is_postgres_runtime():
        return
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS vkpi_employee_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_uid TEXT NOT NULL UNIQUE,
            staff_id INTEGER NOT NULL,
            platform TEXT NOT NULL,
            account_handle TEXT NOT NULL,
            account_display_name TEXT DEFAULT '',
            account_url TEXT DEFAULT '',
            avatar_url TEXT DEFAULT '',
            api_key_encrypted TEXT DEFAULT '',
            api_secret_encrypted TEXT DEFAULT '',
            refresh_token_encrypted TEXT DEFAULT '',
            access_token_encrypted TEXT DEFAULT '',
            token_expiry_at TEXT,
            auth_method TEXT NOT NULL DEFAULT 'manual_api_key',
            self_reported_followers INTEGER DEFAULT 0,
            self_reported_posts INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            last_sync_at TEXT,
            last_sync_status TEXT DEFAULT '',
            last_sync_error TEXT DEFAULT '',
            sync_failure_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted_at TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE (staff_id, platform, account_handle)
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_emp_channels_staff
            ON vkpi_employee_channels(staff_id, status);
        CREATE INDEX IF NOT EXISTS idx_vkpi_emp_channels_platform
            ON vkpi_employee_channels(platform, status);
        CREATE INDEX IF NOT EXISTS idx_vkpi_emp_channels_sync
            ON vkpi_employee_channels(status, last_sync_at);

        CREATE TABLE IF NOT EXISTS vkpi_channel_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER NOT NULL,
            snapshot_date TEXT NOT NULL,
            followers INTEGER NOT NULL DEFAULT 0,
            following INTEGER NOT NULL DEFAULT 0,
            posts_count INTEGER NOT NULL DEFAULT 0,
            total_views INTEGER NOT NULL DEFAULT 0,
            total_likes INTEGER NOT NULL DEFAULT 0,
            total_comments INTEGER NOT NULL DEFAULT 0,
            total_shares INTEGER NOT NULL DEFAULT 0,
            followers_delta INTEGER NOT NULL DEFAULT 0,
            posts_delta INTEGER NOT NULL DEFAULT 0,
            views_delta_24h INTEGER NOT NULL DEFAULT 0,
            likes_delta_24h INTEGER NOT NULL DEFAULT 0,
            engagement_rate REAL DEFAULT 0,
            raw_payload_json TEXT NOT NULL DEFAULT '{}',
            captured_at TEXT NOT NULL,
            UNIQUE (channel_id, snapshot_date)
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_channel_metrics_channel_date
            ON vkpi_channel_metrics(channel_id, snapshot_date DESC);
        CREATE INDEX IF NOT EXISTS idx_vkpi_channel_metrics_date
            ON vkpi_channel_metrics(snapshot_date DESC);

        CREATE TABLE IF NOT EXISTS vkpi_channel_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER,
            staff_id INTEGER,
            action TEXT NOT NULL,
            detail TEXT DEFAULT '',
            ip TEXT DEFAULT '',
            user_agent TEXT DEFAULT '',
            occurred_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_channel_audit_channel
            ON vkpi_channel_audit(channel_id, occurred_at DESC);
        CREATE INDEX IF NOT EXISTS idx_vkpi_channel_audit_staff
            ON vkpi_channel_audit(staff_id, occurred_at DESC);

        CREATE TABLE IF NOT EXISTS vkpi_channel_post_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER NOT NULL,
            snapshot_date TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT '',
            post_uid TEXT NOT NULL,
            post_url TEXT DEFAULT '',
            title TEXT DEFAULT '',
            posted_at TEXT,
            views INTEGER NOT NULL DEFAULT 0,
            likes INTEGER NOT NULL DEFAULT 0,
            comments INTEGER NOT NULL DEFAULT 0,
            shares INTEGER NOT NULL DEFAULT 0,
            views_delta INTEGER NOT NULL DEFAULT 0,
            likes_delta INTEGER NOT NULL DEFAULT 0,
            comments_delta INTEGER NOT NULL DEFAULT 0,
            shares_delta INTEGER NOT NULL DEFAULT 0,
            delta_method TEXT NOT NULL DEFAULT 'post_metric_delta_v1',
            raw_post_json TEXT NOT NULL DEFAULT '{}',
            first_seen_at TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            UNIQUE(channel_id, post_uid, snapshot_date)
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_channel_post_metrics_latest
            ON vkpi_channel_post_metrics(channel_id, post_uid, snapshot_date DESC, captured_at DESC);
        CREATE INDEX IF NOT EXISTS idx_vkpi_channel_post_metrics_channel_date
            ON vkpi_channel_post_metrics(channel_id, snapshot_date DESC);
        CREATE INDEX IF NOT EXISTS idx_vkpi_channel_post_metrics_platform_date
            ON vkpi_channel_post_metrics(platform, snapshot_date DESC);
        """
    )
    conn.commit()
    _SCHEMA_READY = True
