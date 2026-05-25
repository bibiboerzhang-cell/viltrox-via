"""SQLite guard for V-KPI reconciliation tables."""
from __future__ import annotations

from app.db.connection import get_conn, is_postgres_runtime

_SCHEMA_READY = False


def ensure_vkpi_reconciliation_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY or is_postgres_runtime():
        return
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS vkpi_attribution_adjustments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            attribution_id INTEGER NOT NULL,
            adjustment_type TEXT NOT NULL,
            field_changed TEXT DEFAULT '',
            old_value TEXT DEFAULT '',
            new_value TEXT DEFAULT '',
            evidence_text TEXT NOT NULL DEFAULT '',
            evidence_files_json TEXT NOT NULL DEFAULT '[]',
            adjusted_by_staff_id INTEGER,
            adjusted_at TEXT NOT NULL,
            ip TEXT DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_attr_adj_attribution
            ON vkpi_attribution_adjustments(attribution_id, adjusted_at DESC);
        CREATE INDEX IF NOT EXISTS idx_vkpi_attr_adj_staff_time
            ON vkpi_attribution_adjustments(adjusted_by_staff_id, adjusted_at DESC);
        CREATE INDEX IF NOT EXISTS idx_vkpi_attr_adj_type
            ON vkpi_attribution_adjustments(adjustment_type, adjusted_at DESC);

        CREATE TABLE IF NOT EXISTS vkpi_reconciliation_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_platform TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            order_id TEXT DEFAULT '',
            revenue_cents INTEGER NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'USD',
            occurred_at TEXT NOT NULL,
            customer_country TEXT DEFAULT '',
            product_sku TEXT DEFAULT '',
            discount_codes_json TEXT NOT NULL DEFAULT '[]',
            utm_json TEXT NOT NULL DEFAULT '{}',
            landing_referrer TEXT DEFAULT '',
            raw_payload_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending',
            priority INTEGER NOT NULL DEFAULT 0,
            assigned_to_staff_id INTEGER,
            assigned_at TEXT,
            resolved_attribution_id INTEGER,
            resolved_at TEXT,
            resolved_by_staff_id INTEGER,
            rejection_reason TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE (source_platform, source_ref)
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_recon_status_priority
            ON vkpi_reconciliation_queue(status, priority DESC, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_vkpi_recon_assigned
            ON vkpi_reconciliation_queue(assigned_to_staff_id, status);
        CREATE INDEX IF NOT EXISTS idx_vkpi_recon_occurred
            ON vkpi_reconciliation_queue(occurred_at DESC);

        CREATE TABLE IF NOT EXISTS vkpi_shopify_sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sync_uid TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL DEFAULT 'webhook',
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            orders_received INTEGER NOT NULL DEFAULT 0,
            orders_matched INTEGER NOT NULL DEFAULT 0,
            orders_unmatched INTEGER NOT NULL DEFAULT 0,
            orders_failed INTEGER NOT NULL DEFAULT 0,
            error_message TEXT DEFAULT '',
            triggered_by_staff_id INTEGER,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_shopify_sync_time
            ON vkpi_shopify_sync_runs(started_at DESC);
        """
    )
    conn.commit()
    _SCHEMA_READY = True
