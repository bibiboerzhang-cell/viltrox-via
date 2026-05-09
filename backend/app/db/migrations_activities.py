"""
db/migrations_activities.py — SQLite-local Activities Growth OS schema.

Postgres production applies migrations/021_activities.sql. This module keeps
the local SQLite runtime compatible without copying Postgres-only types into
SQLite.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime

logger = get_logger(__name__)


def apply_activities_migrations() -> None:
    if is_postgres_runtime():
        return

    conn = get_conn()
    c = conn.cursor()
    statements = [
        """
        CREATE TABLE IF NOT EXISTS activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            activity_id TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            title_en TEXT DEFAULT '',
            description TEXT DEFAULT '',
            activity_type TEXT NOT NULL DEFAULT 'event',
            market TEXT NOT NULL DEFAULT 'GLOBAL',
            country TEXT DEFAULT '',
            platform TEXT DEFAULT 'offline',
            product_sku TEXT DEFAULT '',
            dealer_name TEXT DEFAULT '',
            dealer_id INTEGER,
            location_name TEXT DEFAULT '',
            location_address TEXT DEFAULT '',
            location_lat REAL,
            location_lng REAL,
            starts_at TEXT NOT NULL,
            ends_at TEXT NOT NULL,
            planned_budget_cents INTEGER NOT NULL DEFAULT 0,
            actual_spend_cents INTEGER NOT NULL DEFAULT 0,
            budget_breakdown_json TEXT NOT NULL DEFAULT '{}',
            qr_token TEXT UNIQUE NOT NULL,
            qr_image_url TEXT DEFAULT '',
            landing_url TEXT DEFAULT '',
            utm_source TEXT DEFAULT 'activity',
            utm_campaign TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'draft',
            submit_attribution_hours INTEGER DEFAULT 168,
            purchase_attribution_hours INTEGER DEFAULT 720,
            eligible_school_id INTEGER,
            require_edu_email INTEGER DEFAULT 0,
            created_by_staff_id INTEGER,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_activities_status_starts ON activities(status, starts_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_activities_qr_token ON activities(qr_token)",
        "CREATE INDEX IF NOT EXISTS idx_activities_market_type ON activities(market, activity_type)",
        "CREATE INDEX IF NOT EXISTS idx_activities_country_platform ON activities(country, platform)",
        "CREATE INDEX IF NOT EXISTS idx_activities_product_sku ON activities(product_sku)",
        """
        CREATE TABLE IF NOT EXISTS activity_attributions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            activity_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
            user_id INTEGER,
            party_id TEXT,
            anonymous_id TEXT,
            event_token TEXT,
            first_scan_at TEXT NOT NULL,
            last_touch_at TEXT,
            first_register_at TEXT,
            edu_verified_at TEXT,
            first_submit_at TEXT,
            first_purchase_at TEXT,
            submission_count INTEGER NOT NULL DEFAULT 0,
            order_count INTEGER NOT NULL DEFAULT 0,
            revenue_cents INTEGER NOT NULL DEFAULT 0,
            commission_cents INTEGER NOT NULL DEFAULT 0,
            attribution_type TEXT NOT NULL DEFAULT 'first_touch',
            manual_attributed_by INTEGER,
            manual_note TEXT DEFAULT '',
            user_agent TEXT DEFAULT '',
            ip_hash TEXT DEFAULT '',
            device_fingerprint TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_act_attr_activity_user_unique
        ON activity_attributions(activity_id, user_id)
        WHERE user_id IS NOT NULL
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_act_attr_activity_anon_unique
        ON activity_attributions(activity_id, anonymous_id)
        WHERE anonymous_id IS NOT NULL AND anonymous_id != ''
        """,
        "CREATE INDEX IF NOT EXISTS idx_act_attr_activity ON activity_attributions(activity_id, first_scan_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_act_attr_user ON activity_attributions(user_id, last_touch_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_act_attr_token ON activity_attributions(event_token)",
        """
        CREATE TABLE IF NOT EXISTS activity_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            activity_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
            user_id INTEGER,
            party_id TEXT,
            anonymous_id TEXT,
            event_token TEXT,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            revenue_cents INTEGER DEFAULT 0,
            commission_cents INTEGER DEFAULT 0,
            user_agent TEXT DEFAULT '',
            ip_hash TEXT DEFAULT '',
            referrer TEXT DEFAULT '',
            source_path TEXT DEFAULT '',
            idempotency_key TEXT,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_act_events_idempotency
        ON activity_events(idempotency_key)
        WHERE idempotency_key IS NOT NULL AND idempotency_key != ''
        """,
        "CREATE INDEX IF NOT EXISTS idx_act_events_activity_time ON activity_events(activity_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_act_events_type ON activity_events(activity_id, event_type, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_act_events_user ON activity_events(user_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_act_events_token ON activity_events(event_token)",
    ]
    applied = 0
    for statement in statements:
        c.execute(statement)
        applied += 1
    conn.commit()
    logger.info("activities sqlite schema ready", extra={"applied": applied})
