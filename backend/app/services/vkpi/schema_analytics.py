"""SQLite guard for V-KPI analytics tables."""
from __future__ import annotations

from app.db.connection import get_conn, is_postgres_runtime

_SCHEMA_READY = False


def ensure_vkpi_analytics_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY or is_postgres_runtime():
        return
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS vkpi_monitored_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_sku TEXT NOT NULL UNIQUE,
            product_name TEXT NOT NULL DEFAULT '',
            series TEXT DEFAULT '',
            mount TEXT DEFAULT '',
            monitor_platforms_json TEXT NOT NULL DEFAULT '["youtube","instagram","tiktok","xiaohongshu"]',
            keywords_json TEXT NOT NULL DEFAULT '[]',
            enabled INTEGER NOT NULL DEFAULT 1,
            last_monitored_at TEXT,
            last_run_id INTEGER,
            created_by_staff_id INTEGER,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_monitored_products_enabled
            ON vkpi_monitored_products(enabled, last_monitored_at);

        CREATE TABLE IF NOT EXISTS vkpi_analytics_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_uid TEXT NOT NULL UNIQUE,
            run_type TEXT NOT NULL,
            triggered_by_staff_id INTEGER,
            triggered_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            target_skus_json TEXT NOT NULL DEFAULT '[]',
            platforms_json TEXT NOT NULL DEFAULT '[]',
            period_start TEXT,
            period_end TEXT,
            summary_json TEXT NOT NULL DEFAULT '{}',
            raw_result_json TEXT NOT NULL DEFAULT '{}',
            cost_usd_cents INTEGER NOT NULL DEFAULT 0,
            error_message TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_analytics_runs_type_time
            ON vkpi_analytics_runs(run_type, triggered_at DESC);
        CREATE INDEX IF NOT EXISTS idx_vkpi_analytics_runs_staff
            ON vkpi_analytics_runs(triggered_by_staff_id, triggered_at DESC);

        CREATE TABLE IF NOT EXISTS vkpi_outreach_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suggestion_uid TEXT NOT NULL UNIQUE,
            source_run_id INTEGER,
            source_product_sku TEXT NOT NULL DEFAULT '',
            detected_at TEXT NOT NULL,
            platform TEXT NOT NULL,
            handle TEXT NOT NULL,
            channel_name TEXT DEFAULT '',
            follower_count INTEGER,
            engagement_rate REAL,
            country_code TEXT DEFAULT '',
            avatar_url TEXT DEFAULT '',
            profile_url TEXT DEFAULT '',
            source_video_url TEXT DEFAULT '',
            source_video_title TEXT DEFAULT '',
            source_view_count INTEGER DEFAULT 0,
            source_like_count INTEGER DEFAULT 0,
            source_published_at TEXT,
            existing_kol_id INTEGER,
            worked_before INTEGER NOT NULL DEFAULT 0,
            last_collab_staff_id INTEGER,
            last_collab_at TEXT,
            mention_count INTEGER NOT NULL DEFAULT 1,
            is_viral INTEGER NOT NULL DEFAULT 0,
            priority INTEGER NOT NULL DEFAULT 0,
            score REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'new',
            claimed_by_staff_id INTEGER,
            claimed_at TEXT,
            dismissed_by_staff_id INTEGER,
            dismissed_at TEXT,
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

        CREATE TABLE IF NOT EXISTS vkpi_staff_outreach_digests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            digest_uid TEXT NOT NULL UNIQUE,
            staff_id INTEGER NOT NULL,
            digest_date TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            item_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'ready',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(staff_id, digest_date)
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_digest_staff_date
            ON vkpi_staff_outreach_digests(staff_id, digest_date DESC);

        CREATE TABLE IF NOT EXISTS vkpi_staff_outreach_digest_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            digest_id INTEGER NOT NULL,
            suggestion_id INTEGER NOT NULL,
            rank INTEGER NOT NULL,
            quality_score REAL NOT NULL DEFAULT 0,
            relevance_reason TEXT NOT NULL DEFAULT '',
            buyer_profile TEXT NOT NULL DEFAULT '',
            viewer_profile TEXT NOT NULL DEFAULT '',
            content_angle TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE(digest_id, suggestion_id)
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_digest_items_digest_rank
            ON vkpi_staff_outreach_digest_items(digest_id, rank);
        """
    )
    conn.commit()
    _SCHEMA_READY = True
