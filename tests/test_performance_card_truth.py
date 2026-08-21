from __future__ import annotations

import sqlite3

from app.domains.kol import performance_card


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _ShopifyConn:
    def __init__(self):
        self.sql = ""
        self.params = ()

    def execute(self, sql, params=()):
        self.sql = " ".join(str(sql).split())
        self.params = tuple(params)
        return _Rows([{"currency": "USD", "orders": 2, "gmv_cents": 12000}])


def test_shopify_block_uses_only_canonical_verified_truth_predicate(monkeypatch):
    conn = _ShopifyConn()
    monkeypatch.setattr("app.db.connection.table_exists", lambda _name: True)

    result = performance_card._shopify_block(conn, 77)

    assert result["available"] is True
    assert result["truth_status"] == "provider_verified_shopify"
    assert result["orders"] == 2
    assert result["gmv_cents"] == 12000
    assert conn.params == (77,)
    assert "FROM vkpi_sales_attributions s" in conn.sql
    assert "s.kol_id = ?" in conn.sql
    assert "truth_shopify_order.provider_auth_mode='shopify-hmac'" in conn.sql
    assert "truth_shopify_order.provider_verified_at IS NOT NULL" in conn.sql
    assert "truth_shopify_order.raw_payload_hash" in conn.sql
    assert "COUNT(DISTINCT s.shopify_order_snapshot_id) AS orders" in conn.sql
    assert "FROM vkpi_shopify_orders" not in conn.sql


def test_shopify_order_and_refund_rows_share_one_distinct_order(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_shopify_order_snapshots (
          id INTEGER PRIMARY KEY,
          provider_auth_mode TEXT,
          provider_verified_at TEXT,
          raw_payload_hash TEXT,
          financial_status TEXT,
          cancelled_at TEXT
        );
        CREATE TABLE vkpi_sales_attributions (
          id INTEGER PRIMARY KEY,
          kol_id INTEGER,
          currency TEXT,
          source_platform TEXT,
          confidence TEXT,
          shopify_order_snapshot_id INTEGER,
          revenue_cents INTEGER
        );
        """
    )
    conn.execute(
        "INSERT INTO vkpi_shopify_order_snapshots VALUES (?, ?, ?, ?, ?, ?)",
        (1, "shopify-hmac", "2026-08-21T00:00:00Z", "signed-hash", "partially_refunded", None),
    )
    conn.executemany(
        "INSERT INTO vkpi_sales_attributions VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (1, 77, "USD", "shopify", "confirmed", 1, 12000),
            (2, 77, "USD", "shopify", "refund", 1, -2000),
        ],
    )
    monkeypatch.setattr("app.db.connection.table_exists", lambda _name: True)

    result = performance_card._shopify_block(conn, 77)

    assert result["orders"] == 1
    assert result["gmv_cents"] == 10000


def test_commerce_sources_are_not_added_without_cross_source_key(monkeypatch):
    monkeypatch.setattr(
        performance_card,
        "_goaffpro_block",
        lambda *_args, **_kwargs: {"available": True, "clicks": 10, "orders": 2, "gmv_cents": 5000},
    )
    monkeypatch.setattr(
        performance_card,
        "_shopify_block",
        lambda *_args, **_kwargs: {"available": True, "orders": 2, "gmv_cents": 5000},
    )
    monkeypatch.setattr(
        performance_card,
        "_short_links_block",
        lambda *_args, **_kwargs: {"available": True, "links": 1, "clicks": 10, "orders": 2},
    )

    result = performance_card._commerce_block(object(), 5, 99)

    assert result["status"] == "partial"
    assert result["partial"] is True
    assert result["has_signal"] is True
    assert result["total_clicks"] is None
    assert result["total_orders"] is None
    assert result["dedupe_status"] == "unavailable_no_canonical_cross_source_key"
    assert result["available_sources"] == ["goaffpro", "shopify", "short_links"]


def test_share_text_never_claims_partial_cross_source_totals():
    commerce = {
        "total_clicks": 20,
        "total_orders": 6,
        "partial": True,
        "aggregate_usable_for_share": False,
    }
    text = performance_card._share_text(
        {"display_name": "Creator"},
        {
            "branded_videos": 1,
            "total_views": 100,
            "engagement_total": 10,
            "first_video_at": "2026-08-01",
            "top_video": {},
        },
        commerce,
        {"project_count": 1},
    )

    assert "20 次访问" not in text["zh"]
    assert "6 笔订单" not in text["zh"]
    assert "20 visits" not in text["en"]
    assert "6 orders" not in text["en"]


def test_content_all_null_metrics_are_partial_null_not_false_zero(monkeypatch):
    monkeypatch.setattr(
        performance_card,
        "_load_evidence_rows",
        lambda *_args, **_kwargs: [
            {
                "title": "Viltrox review",
                "content_url": "https://example.test/video",
                "platform": "youtube",
                "view_count": None,
                "like_count": None,
                "comment_count": None,
                "published_at": "2026-08-20T00:00:00Z",
            }
        ],
    )
    monkeypatch.setattr(performance_card, "_deep_analyzed_count", lambda *_args, **_kwargs: 0)

    result = performance_card._content_block(object(), 9)

    assert result["status"] == "partial"
    assert result["total_views"] is None
    assert result["total_likes"] is None
    assert result["total_comments"] is None
    assert result["engagement_total"] is None
    assert result["metric_coverage"]["views"] == {"observed": 0, "total": 1}


def test_scoped_shopify_and_short_links_require_visible_project_ids(monkeypatch):
    shopify = _ShopifyConn()
    monkeypatch.setattr("app.db.connection.table_exists", lambda _name: True)

    performance_card._shopify_block(shopify, 77, project_ids=[10, 20])

    assert "s.project_id IN (?,?)" in shopify.sql
    assert shopify.params == (77, 10, 20)

    class _One:
        def fetchone(self):
            return {"links": 1, "clicks": 2, "orders": 1}

    class _ShortConn:
        sql = ""
        params = ()

        def execute(self, sql, params=()):
            self.sql = " ".join(str(sql).split())
            self.params = tuple(params)
            return _One()

    short = _ShortConn()
    performance_card._short_links_block(short, 77, project_ids=[10, 20])

    assert "l.project_id IN (?,?)" in short.sql
    assert "l2.project_id IN (?,?)" in short.sql
    assert "l3.project_id IN (?,?)" in short.sql
    assert short.params == (77, 10, 20, 77, 10, 20, 77, 10, 20)


def test_timeline_query_is_restricted_to_visible_project_ids(monkeypatch):
    class _RowsResult:
        def fetchall(self):
            return []

    class _TimelineConn:
        sql = ""
        params = ()

        def execute(self, sql, params=()):
            self.sql = " ".join(str(sql).split())
            self.params = tuple(params)
            return _RowsResult()

    conn = _TimelineConn()
    monkeypatch.setattr("app.db.connection.table_exists", lambda name: name == "vkpi_project_kol_assignments")

    performance_card._timeline_block(conn, 9, {}, project_ids=[10, 20])

    assert "a.project_id IN (?,?)" in conn.sql
    assert conn.params == (9, 10, 20)


def test_content_evidence_query_is_restricted_to_visible_project_ids():
    class _RowsResult:
        def fetchall(self):
            return []

    class _EvidenceConn:
        sql = ""
        params = ()

        def execute(self, sql, params=()):
            self.sql = " ".join(str(sql).split())
            self.params = tuple(params)
            return _RowsResult()

    conn = _EvidenceConn()

    performance_card._load_evidence_rows(conn, 9, project_ids=[10, 20])

    assert "project_id IN (?,?)" in conn.sql
    assert conn.params == (9, 10, 20, 500)
