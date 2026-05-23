"""Local SQLite guard for the V-KPI core tables.

Postgres uses migrations/023_vkpi_core.sql. Local demo runs can start from an
older SQLite database, so admin endpoints call this guard before reading V-KPI
tables.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime

_SCHEMA_READY = False
logger = get_logger(__name__)


def _table_columns(conn, table_name: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    except Exception as exc:
        logger.warning("vkpi schema column lookup failed for %s: %s", table_name, exc)
        return set()


def _ensure_column(conn, table_name: str, column_name: str, column_def: str) -> None:
    if column_name in _table_columns(conn, table_name):
        return
    try:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")
    except Exception as exc:
        logger.warning("vkpi schema add column skipped for %s.%s: %s", table_name, column_name, exc)


def ensure_vkpi_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY or is_postgres_runtime():
        return
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS vkpi_projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_uid TEXT NOT NULL UNIQUE,
            project_name TEXT NOT NULL,
            kol_id INTEGER,
            assigned_staff_id INTEGER,
            created_by_staff_id INTEGER,
            product_sku TEXT DEFAULT '',
            product_name TEXT DEFAULT '',
            platform TEXT DEFAULT '',
            marketplace TEXT DEFAULT '',
            stage TEXT NOT NULL DEFAULT 'discovery',
            stage_status TEXT NOT NULL DEFAULT 'active',
            priority TEXT NOT NULL DEFAULT 'normal',
            source_type TEXT DEFAULT 'manual',
            shopify_discount_code TEXT DEFAULT '',
            shopify_link TEXT DEFAULT '',
            amazon_asin TEXT DEFAULT '',
            amazon_attribution_link TEXT DEFAULT '',
            amazon_associates_link TEXT DEFAULT '',
            sample_status TEXT DEFAULT 'not_required',
            tracking_number TEXT DEFAULT '',
            target_post_date TEXT,
            due_at TEXT,
            started_at TEXT,
            closed_at TEXT,
            last_activity_at TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_projects_stage ON vkpi_projects(stage, stage_status);
        CREATE INDEX IF NOT EXISTS idx_vkpi_projects_staff ON vkpi_projects(assigned_staff_id, updated_at DESC);

        CREATE TABLE IF NOT EXISTS vkpi_project_stage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            from_stage TEXT DEFAULT '',
            to_stage TEXT NOT NULL,
            event_type TEXT NOT NULL DEFAULT 'stage_change',
            actor_staff_id INTEGER,
            note TEXT DEFAULT '',
            source_ref_type TEXT DEFAULT '',
            source_ref_id TEXT DEFAULT '',
            effective_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_stage_events_project ON vkpi_project_stage_events(project_id, effective_at DESC);

        CREATE TABLE IF NOT EXISTS vkpi_kol_claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_id INTEGER NOT NULL,
            staff_id INTEGER NOT NULL,
            project_id INTEGER,
            status TEXT NOT NULL DEFAULT 'active',
            claimed_at TEXT NOT NULL,
            expires_at TEXT,
            last_effective_touch_at TEXT,
            release_reason TEXT DEFAULT '',
            released_at TEXT,
            released_by_staff_id INTEGER,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_vkpi_kol_claims_one_active
            ON vkpi_kol_claims(kol_id)
            WHERE status = 'active';

        CREATE TABLE IF NOT EXISTS vkpi_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link_uid TEXT NOT NULL UNIQUE,
            slug TEXT NOT NULL UNIQUE,
            link_type TEXT NOT NULL DEFAULT 'generic',
            destination_url TEXT NOT NULL,
            fallback_url TEXT DEFAULT '',
            platform TEXT DEFAULT '',
            product_sku TEXT DEFAULT '',
            campaign_name TEXT DEFAULT '',
            kol_id INTEGER,
            project_id INTEGER,
            staff_id INTEGER,
            created_by_staff_id INTEGER,
            status TEXT NOT NULL DEFAULT 'live',
            redirect_mode INTEGER NOT NULL DEFAULT 302,
            allowlist_status TEXT NOT NULL DEFAULT 'allowed',
            bot_filter_mode TEXT NOT NULL DEFAULT 'standard',
            utm_source TEXT DEFAULT '',
            utm_medium TEXT DEFAULT '',
            utm_campaign TEXT DEFAULT '',
            utm_content TEXT DEFAULT '',
            starts_at TEXT,
            expires_at TEXT,
            health_status TEXT NOT NULL DEFAULT 'unknown',
            last_health_checked_at TEXT,
            click_count INTEGER NOT NULL DEFAULT 0,
            valid_click_count INTEGER NOT NULL DEFAULT 0,
            bot_click_count INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_links_project ON vkpi_links(project_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_vkpi_links_staff ON vkpi_links(staff_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS vkpi_link_destinations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link_id INTEGER NOT NULL,
            country_code TEXT DEFAULT '',
            device_type TEXT DEFAULT '',
            weight INTEGER NOT NULL DEFAULT 100,
            destination_url TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vkpi_link_clicks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link_id INTEGER NOT NULL,
            event_id TEXT NOT NULL UNIQUE,
            clicked_at TEXT NOT NULL,
            ip_hash TEXT DEFAULT '',
            user_agent TEXT DEFAULT '',
            referrer TEXT DEFAULT '',
            country_code TEXT DEFAULT '',
            device_type TEXT DEFAULT '',
            bot_score INTEGER NOT NULL DEFAULT 0,
            is_bot INTEGER NOT NULL DEFAULT 0,
            is_unique INTEGER NOT NULL DEFAULT 0,
            session_id TEXT DEFAULT '',
            destination_url TEXT NOT NULL,
            order_id INTEGER,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_clicks_link_time ON vkpi_link_clicks(link_id, clicked_at DESC);

        CREATE TABLE IF NOT EXISTS vkpi_sales_attributions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_platform TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            project_id INTEGER,
            link_id INTEGER,
            kol_id INTEGER,
            staff_id INTEGER,
            shopify_order_snapshot_id INTEGER,
            product_sku TEXT DEFAULT '',
            order_id INTEGER,
            amazon_campaign_id TEXT DEFAULT '',
            revenue_cents INTEGER NOT NULL DEFAULT 0,
            commission_cents INTEGER NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'USD',
            attribution_model TEXT NOT NULL DEFAULT 'last_touch',
            confidence TEXT NOT NULL DEFAULT 'confirmed',
            occurred_at TEXT,
            imported_at TEXT NOT NULL,
            evidence_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE(source_platform, source_ref)
        );

        CREATE TABLE IF NOT EXISTS vkpi_shopify_order_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shopify_order_id TEXT NOT NULL UNIQUE,
            admin_graphql_api_id TEXT DEFAULT '',
            order_name TEXT DEFAULT '',
            order_number TEXT DEFAULT '',
            processed_at TEXT DEFAULT '',
            currency TEXT DEFAULT 'USD',
            subtotal_cents INTEGER NOT NULL DEFAULT 0,
            total_cents INTEGER NOT NULL DEFAULT 0,
            financial_status TEXT DEFAULT '',
            fulfillment_status TEXT DEFAULT '',
            refund_status TEXT DEFAULT '',
            discount_codes_json TEXT NOT NULL DEFAULT '[]',
            landing_site TEXT DEFAULT '',
            note_attributes_json TEXT NOT NULL DEFAULT '{}',
            line_items_json TEXT NOT NULL DEFAULT '[]',
            raw_payload_hash TEXT DEFAULT '',
            raw_payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vkpi_cost_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            kol_id INTEGER,
            staff_id INTEGER,
            cost_type TEXT NOT NULL,
            amount_cents INTEGER NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'USD',
            status TEXT NOT NULL DEFAULT 'actual',
            incurred_at TEXT NOT NULL,
            source_ref TEXT DEFAULT '',
            note TEXT DEFAULT '',
            created_by_staff_id INTEGER,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vkpi_product_cost_catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_sku TEXT NOT NULL UNIQUE,
            product_name TEXT DEFAULT '',
            unit_cost_cents INTEGER NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'USD',
            active INTEGER NOT NULL DEFAULT 1,
            note TEXT DEFAULT '',
            created_by_staff_id INTEGER,
            updated_by_staff_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_product_cost_catalog_active
            ON vkpi_product_cost_catalog(active, product_sku);

        CREATE TABLE IF NOT EXISTS vkpi_kpi_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ledger_date TEXT NOT NULL,
            staff_id INTEGER,
            kol_id INTEGER,
            project_id INTEGER,
            metric_key TEXT NOT NULL,
            metric_value REAL NOT NULL DEFAULT 0,
            source_type TEXT NOT NULL DEFAULT 'system',
            source_ref TEXT DEFAULT '',
            confidence TEXT NOT NULL DEFAULT 'confirmed',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vkpi_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_key TEXT NOT NULL UNIQUE,
            severity TEXT NOT NULL DEFAULT 'info',
            status TEXT NOT NULL DEFAULT 'open',
            target_type TEXT NOT NULL DEFAULT '',
            target_id INTEGER,
            staff_id INTEGER,
            title TEXT NOT NULL,
            body TEXT DEFAULT '',
            rule_key TEXT DEFAULT '',
            due_at TEXT,
            resolved_at TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_alerts_status ON vkpi_alerts(status, severity, created_at DESC);

        CREATE TABLE IF NOT EXISTS vkpi_decision_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            window_key TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE(snapshot_date, window_key)
        );

        CREATE TABLE IF NOT EXISTS vkpi_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            kol_id INTEGER,
            staff_id INTEGER,
            source TEXT NOT NULL DEFAULT 'manual',
            direction TEXT NOT NULL DEFAULT 'outbound',
            sender TEXT DEFAULT '',
            receiver TEXT DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            snippet TEXT DEFAULT '',
            evidence_url TEXT DEFAULT '',
            follow_up_due_at TEXT,
            captured_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_messages_project ON vkpi_messages(project_id, captured_at DESC);

        CREATE TABLE IF NOT EXISTS vkpi_message_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL,
            file_url TEXT NOT NULL,
            file_type TEXT DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vkpi_content_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            kol_id INTEGER,
            link_id INTEGER,
            platform TEXT NOT NULL DEFAULT '',
            post_url TEXT NOT NULL,
            title TEXT DEFAULT '',
            thumbnail_url TEXT DEFAULT '',
            published_at TEXT,
            content_type TEXT DEFAULT '',
            views INTEGER NOT NULL DEFAULT 0,
            likes INTEGER NOT NULL DEFAULT 0,
            comments INTEGER NOT NULL DEFAULT 0,
            shares INTEGER NOT NULL DEFAULT 0,
            rights_status TEXT DEFAULT 'unknown',
            ad_usage_allowed INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(project_id, post_url)
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_content_posts_project ON vkpi_content_posts(project_id, published_at DESC);

        CREATE TABLE IF NOT EXISTS vkpi_content_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER,
            project_id INTEGER,
            asset_url TEXT NOT NULL,
            asset_type TEXT DEFAULT '',
            usage_rights TEXT DEFAULT 'unknown',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vkpi_project_terms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL UNIQUE,
            cash_fee_cents INTEGER NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'USD',
            sample_terms TEXT DEFAULT '',
            deliverables_json TEXT NOT NULL DEFAULT '[]',
            usage_rights TEXT DEFAULT '',
            due_at TEXT,
            note TEXT DEFAULT '',
            created_by_staff_id INTEGER,
            updated_by_staff_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vkpi_project_deliverables (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            deliverable_type TEXT NOT NULL DEFAULT 'video',
            quantity INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'planned',
            due_at TEXT,
            delivered_at TEXT,
            evidence_url TEXT DEFAULT '',
            note TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vkpi_sample_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            kol_id INTEGER,
            product_sku TEXT DEFAULT '',
            product_name TEXT DEFAULT '',
            serial_number TEXT DEFAULT '',
            sample_cost_cents INTEGER NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'USD',
            return_required INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'planned',
            shipped_at TEXT,
            received_at TEXT,
            returned_at TEXT,
            note TEXT DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_sample_assets_project ON vkpi_sample_assets(project_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS vkpi_shipments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            sample_asset_id INTEGER,
            carrier TEXT DEFAULT '',
            tracking_number TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'planned',
            shipping_cost_cents INTEGER NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'USD',
            shipped_at TEXT,
            delivered_at TEXT,
            evidence_url TEXT DEFAULT '',
            note TEXT DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_shipments_project ON vkpi_shipments(project_id, created_at DESC);
        """
    )
    _ensure_column(conn, "vkpi_sales_attributions", "shopify_order_snapshot_id", "INTEGER")
    if _table_columns(conn, "kols"):
        _ensure_column(conn, "kols", "avatar_url", "TEXT DEFAULT ''")
        _ensure_column(conn, "kols", "profile_url", "TEXT DEFAULT ''")
        _ensure_column(conn, "kols", "contact_links_json", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "kols", "contact_raw_json", "TEXT NOT NULL DEFAULT '{}'")
    conn.commit()
    _SCHEMA_READY = True
