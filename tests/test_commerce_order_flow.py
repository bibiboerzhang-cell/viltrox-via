from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.commerce import orders as orders_mod  # noqa: E402


SCHEMA = """
CREATE TABLE platform_ingest_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT NOT NULL UNIQUE,
    source_platform TEXT NOT NULL,
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    external_id TEXT DEFAULT '',
    creator_handle TEXT DEFAULT '',
    region_code TEXT DEFAULT '',
    ingest_status TEXT DEFAULT 'queued',
    payload_json TEXT DEFAULT '{}',
    occurred_at TEXT NOT NULL,
    processed_at TEXT DEFAULT '',
    error_message TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    ingested_into_orders_at TEXT
);

CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT,
    creator_code TEXT
);

CREATE TABLE student_verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    student_id_code TEXT,
    status TEXT
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_order_id TEXT UNIQUE NOT NULL,
    source_platform TEXT NOT NULL,
    customer_email TEXT,
    customer_country TEXT,
    subtotal_cents INTEGER NOT NULL DEFAULT 0,
    currency TEXT DEFAULT 'USD',
    items_json TEXT,
    attribution_source TEXT,
    attribution_type TEXT,
    attribution_user_id INTEGER,
    utm_source TEXT,
    utm_medium TEXT,
    utm_campaign TEXT,
    commission_rate_bps INTEGER DEFAULT 0,
    commission_cents INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'paid',
    placed_at TEXT NOT NULL,
    webhook_event_ids_json TEXT,
    raw_payload TEXT,
    flagged_reason TEXT,
    flagged_by INTEGER,
    flagged_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT
);

CREATE TABLE attribution_clicks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref_code TEXT NOT NULL,
    converted_to_order_id INTEGER,
    clicked_at TEXT NOT NULL
);
"""


class CommerceOrderFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "commerce.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.get_conn_patch = patch.object(orders_mod, "get_conn", return_value=self.conn)
        self.emit_patch = patch.object(orders_mod, "_emit_purchase_to_party_layer", return_value=None)
        self.get_conn_patch.start()
        self.emit_patch.start()

    def tearDown(self) -> None:
        self.emit_patch.stop()
        self.get_conn_patch.stop()
        self.conn.close()
        self.tmp.cleanup()

    def seed_shopify_event(self, external_id: str, occurred_at: str) -> int:
        payload = {
            "id": external_id,
            "subtotal_price": "120.50",
            "currency": "USD",
            "customer": {"email": "creator@example.com"},
            "shipping_address": {"country_code": "US"},
            "line_items": [{"sku": "AF-75", "title": "AF 75mm", "quantity": 1, "price": "120.50"}],
        }
        cur = self.conn.execute(
            """
            INSERT INTO platform_ingest_events (
                dedupe_key, source_platform, event_type, entity_type, external_id,
                ingest_status, payload_json, occurred_at, created_at
            ) VALUES (?, 'shopify', 'orders_paid', 'order', ?, 'done', ?, ?, ?)
            """,
            (
                f"shopify:orders_paid:{external_id}",
                external_id,
                json.dumps(payload),
                occurred_at,
                occurred_at,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def test_shopify_order_ingest_is_idempotent(self) -> None:
        event_id = self.seed_shopify_event("order-1001", "2026-04-29T10:00:00Z")

        first_order_id = asyncio.run(orders_mod.ingest_order_from_event(event_id))
        second_order_id = asyncio.run(orders_mod.ingest_order_from_event(event_id))

        self.assertEqual(first_order_id, second_order_id)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS n FROM orders WHERE external_order_id='order-1001'").fetchone()["n"],
            1,
        )
        stamped = self.conn.execute(
            "SELECT ingested_into_orders_at FROM platform_ingest_events WHERE id=?",
            (event_id,),
        ).fetchone()
        self.assertTrue(stamped["ingested_into_orders_at"])

    def test_backfill_respects_cutoff_by_default(self) -> None:
        self.seed_shopify_event("order-old", "2026-04-01T10:00:00Z")
        self.seed_shopify_event("order-new", "2026-04-29T10:00:00Z")

        result = asyncio.run(
            orders_mod.backfill_orders_from_events(
                limit=10,
                cutoff_at="2026-04-15T00:00:00Z",
            )
        )

        self.assertEqual(result["scanned"], 1)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS n FROM orders WHERE external_order_id='order-new'").fetchone()["n"],
            1,
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS n FROM orders WHERE external_order_id='order-old'").fetchone()["n"],
            0,
        )


if __name__ == "__main__":
    unittest.main()
