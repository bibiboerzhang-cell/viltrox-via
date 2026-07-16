#!/usr/bin/env python3
"""Smoke test for V-KPI Shopify order snapshot and refund bridge.

Creates temporary KOL/project/link/click rows, sends Shopify order/refund
webhooks to the local API, verifies lineage drilldown, and cleans all rows that
contain the smoke marker.
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("DB_RUNTIME_BACKEND", "sqlite")
os.environ.setdefault("DATABASE_URL", "")

from app.core.config import PLATFORM_INGEST_SHARED_SECRET
from app.core.security import make_token
from app.db.connection import get_conn
from app.services.vkpi.schema import ensure_vkpi_schema
from app.domains.lineage import ensure_vkpi_lineage_schema
from app.services.vkpi.schema_reconciliation import ensure_vkpi_reconciliation_schema

BASE = "http://127.0.0.1:8102"
PREFIX = "vkpi-shopify-bridge-smoke-"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Smoke:
    def __init__(self) -> None:
        self.marker = f"{PREFIX}{int(time.time())}"
        self.now = _now()
        self.conn = get_conn()
        self.order_numeric_id = str(int(time.time() * 1000))
        self.order_gid = f"gid://shopify/Order/{self.order_numeric_id}"
        self.refund_gid = f"gid://shopify/Refund/{self.order_numeric_id}"
        self.click_id = f"{self.marker}-click"
        self.slug = f"{self.marker}-slug"
        self.sku = f"{self.marker}-sku"
        self.user_id = 0
        self.staff_id = 0
        self.token = ""

    def post(self, path: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
        data = _json(payload).encode("utf-8")
        req = urllib.request.Request(BASE + path, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        if PLATFORM_INGEST_SHARED_SECRET:
            req.add_header("X-Viltrox-Ingest-Secret", PLATFORM_INGEST_SHARED_SECRET)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"HTTP {exc.code} {path}: {body[:300]}") from exc

    def admin_post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = _json(payload).encode("utf-8")
        req = urllib.request.Request(BASE + path, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"HTTP {exc.code} {path}: {body[:300]}") from exc

    def get(self, path: str) -> dict[str, Any]:
        req = urllib.request.Request(BASE + path, method="GET")
        req.add_header("Authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def cleanup(self, marker: str | None = None) -> dict[str, int]:
        marker = marker or self.marker
        conn = self.conn
        source_rows = conn.execute(
            "SELECT metric_value_id FROM vkpi_metric_sources WHERE snapshot_json LIKE ? OR evidence_ref LIKE ?",
            (f"%{marker}%", f"%{marker}%"),
        ).fetchall()
        value_ids = sorted({int(row["metric_value_id"]) for row in source_rows if row["metric_value_id"] is not None})
        run_ids: list[int] = []
        if value_ids:
            ph = ",".join("?" for _ in value_ids)
            run_ids = [int(row["run_id"]) for row in conn.execute(f"SELECT DISTINCT run_id FROM vkpi_metric_values WHERE id IN ({ph})", value_ids).fetchall()]
            conn.execute(f"DELETE FROM vkpi_metric_sources WHERE metric_value_id IN ({ph})", value_ids)
            if run_ids:
                phr = ",".join("?" for _ in run_ids)
                conn.execute(f"DELETE FROM vkpi_metric_values WHERE run_id IN ({phr})", run_ids)
                conn.execute(f"DELETE FROM vkpi_metric_runs WHERE id IN ({phr})", run_ids)

        attr_ids = [
            int(row["id"])
            for row in conn.execute(
                """
                SELECT id FROM vkpi_sales_attributions
                WHERE source_ref LIKE ? OR evidence_json LIKE ? OR product_sku LIKE ?
                   OR shopify_order_snapshot_id IN (
                       SELECT id FROM vkpi_shopify_order_snapshots WHERE raw_payload_json LIKE ?
                   )
                """,
                (f"%{marker}%", f"%{marker}%", f"%{marker}%", f"%{marker}%"),
            ).fetchall()
        ]
        if attr_ids:
            ph = ",".join("?" for _ in attr_ids)
            conn.execute(f"DELETE FROM vkpi_attribution_adjustments WHERE attribution_id IN ({ph})", attr_ids)
            conn.execute(f"DELETE FROM vkpi_sales_attributions WHERE id IN ({ph})", attr_ids)
        conn.execute("DELETE FROM vkpi_reconciliation_queue WHERE source_ref LIKE ? OR raw_payload_json LIKE ?", (f"%{marker}%", f"%{marker}%"))
        conn.execute("DELETE FROM vkpi_link_clicks WHERE event_id LIKE ? OR metadata_json LIKE ?", (f"%{marker}%", f"%{marker}%"))
        conn.execute("DELETE FROM vkpi_links WHERE slug LIKE ? OR campaign_name LIKE ? OR metadata_json LIKE ?", (f"%{marker}%", f"%{marker}%", f"%{marker}%"))
        conn.execute("DELETE FROM vkpi_project_stage_events WHERE project_id IN (SELECT id FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?)", (f"%{marker}%", f"%{marker}%", f"%{marker}%"))
        conn.execute("DELETE FROM vkpi_cost_ledger WHERE project_id IN (SELECT id FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?)", (f"%{marker}%", f"%{marker}%", f"%{marker}%"))
        conn.execute("DELETE FROM vkpi_content_posts WHERE project_id IN (SELECT id FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?)", (f"%{marker}%", f"%{marker}%", f"%{marker}%"))
        conn.execute("DELETE FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?", (f"%{marker}%", f"%{marker}%", f"%{marker}%"))
        conn.execute("DELETE FROM vkpi_kol_claims WHERE kol_id IN (SELECT id FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ?)", (f"%{marker}%", f"%{marker}%"))
        conn.execute("DELETE FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ?", (f"%{marker}%", f"%{marker}%"))
        conn.execute("DELETE FROM vkpi_shopify_order_snapshots WHERE raw_payload_json LIKE ? OR order_name LIKE ?", (f"%{marker}%", f"%{marker}%"))
        conn.execute("DELETE FROM vkpi_shopify_sync_runs WHERE metadata_json LIKE ? OR sync_uid LIKE ?", (f"%{marker}%", f"%{marker}%"))
        conn.execute("DELETE FROM vkpi_sensitive_access_logs WHERE metadata_json LIKE ? OR resource_id LIKE ? OR page_path LIKE ?", (f"%{marker}%", f"%{marker}%", f"%{marker}%"))
        conn.execute("DELETE FROM vkpi_business_audit_logs WHERE metadata_json LIKE ? OR detail LIKE ? OR target_id LIKE ?", (f"%{marker}%", f"%{marker}%", f"%{marker}%"))
        conn.execute("DELETE FROM staff WHERE id=? OR user_id=?", (self.staff_id or -1, self.user_id or -1))
        conn.execute("DELETE FROM users WHERE id=?", (self.user_id or -1,))
        conn.commit()
        return {
            "users": int(conn.execute("SELECT COUNT(*) AS n FROM users WHERE email LIKE ?", (f"%{marker}%",)).fetchone()["n"]),
            "metric_sources": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_metric_sources WHERE snapshot_json LIKE ? OR evidence_ref LIKE ?", (f"%{marker}%", f"%{marker}%")).fetchone()["n"]),
            "attributions": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_sales_attributions WHERE source_ref LIKE ? OR evidence_json LIKE ? OR product_sku LIKE ?", (f"%{marker}%", f"%{marker}%", f"%{marker}%")).fetchone()["n"]),
            "snapshots": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_shopify_order_snapshots WHERE raw_payload_json LIKE ? OR order_name LIKE ?", (f"%{marker}%", f"%{marker}%")).fetchone()["n"]),
            "projects": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?", (f"%{marker}%", f"%{marker}%", f"%{marker}%")).fetchone()["n"]),
            "kols": int(conn.execute("SELECT COUNT(*) AS n FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ?", (f"%{marker}%", f"%{marker}%")).fetchone()["n"]),
            "links": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_links WHERE slug LIKE ? OR campaign_name LIKE ? OR metadata_json LIKE ?", (f"%{marker}%", f"%{marker}%", f"%{marker}%")).fetchone()["n"]),
            "sync_runs": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_shopify_sync_runs WHERE metadata_json LIKE ? OR sync_uid LIKE ?", (f"%{marker}%", f"%{marker}%")).fetchone()["n"]),
            "sensitive_audit": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_sensitive_access_logs WHERE metadata_json LIKE ? OR resource_id LIKE ? OR page_path LIKE ?", (f"%{marker}%", f"%{marker}%", f"%{marker}%")).fetchone()["n"]),
            "business_audit": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE metadata_json LIKE ? OR detail LIKE ? OR target_id LIKE ?", (f"%{marker}%", f"%{marker}%", f"%{marker}%")).fetchone()["n"]),
            "metric_runs_deleted": len(run_ids),
        }

    def seed_actor(self) -> None:
        conn = self.conn
        email = f"{self.marker}@viltrox-smoke.local"
        conn.execute(
            "INSERT INTO users (created_at, email, password_hash, name, status, role, email_verified, avatar_url) VALUES (?,?,?,?,?,?,?,?)",
            (self.now, email, "v2:00:00", self.marker, "approved", "admin", 1, f"https://avatar.example/{self.marker}.png"),
        )
        self.user_id = int(conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"])
        conn.execute(
            "INSERT INTO staff (user_id, role, permissions_json, active, invited_at, accepted_at, last_active_at) VALUES (?,?,?,?,?,?,?)",
            (self.user_id, "admin", _json({"vkpi": "write"}), 1, self.now, self.now, self.now),
        )
        self.staff_id = int(conn.execute("SELECT id FROM staff WHERE user_id=?", (self.user_id,)).fetchone()["id"])
        conn.commit()
        self.token = make_token(self.user_id, "admin")

    def seed(self) -> tuple[int, int, int]:
        conn = self.conn
        self.seed_actor()
        conn.execute(
            "INSERT INTO kols (channel_name, channel_url, platform, contact_email, assigned_staff_id, created_by_staff_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (self.marker, f"https://instagram.com/{self.marker}", "instagram", f"{self.marker}@example.com", self.staff_id, self.staff_id, self.now, self.now),
        )
        kol_id = int(conn.execute("SELECT id FROM kols WHERE channel_name=?", (self.marker,)).fetchone()["id"])
        conn.execute(
            "INSERT INTO vkpi_projects (project_uid, project_name, kol_id, assigned_staff_id, created_by_staff_id, product_sku, product_name, platform, stage, stage_status, started_at, last_activity_at, metadata_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.marker, f"{self.marker} project", kol_id, self.staff_id, self.staff_id, self.sku, "Smoke Lens", "instagram", "published", "active", self.now, self.now, _json({"marker": self.marker}), self.now, self.now),
        )
        project_id = int(conn.execute("SELECT id FROM vkpi_projects WHERE project_uid=?", (self.marker,)).fetchone()["id"])
        conn.execute(
            "INSERT INTO vkpi_links (link_uid, slug, link_type, destination_url, platform, product_sku, campaign_name, kol_id, project_id, staff_id, created_by_staff_id, status, redirect_mode, allowlist_status, bot_filter_mode, utm_source, utm_medium, utm_campaign, utm_content, click_count, valid_click_count, bot_click_count, metadata_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.marker, self.slug, "shopify", f"https://viltrox.com/products/{self.marker}", "shopify", self.sku, self.marker, kol_id, project_id, self.staff_id, self.staff_id, "active", 302, "allowed", "standard", "vkpi", "kol", self.marker, "smoke", 1, 1, 0, _json({"marker": self.marker}), self.now, self.now),
        )
        link_id = int(conn.execute("SELECT id FROM vkpi_links WHERE slug=?", (self.slug,)).fetchone()["id"])
        conn.execute(
            "INSERT INTO vkpi_link_clicks (link_id, event_id, clicked_at, ip_hash, user_agent, referrer, country_code, device_type, bot_score, is_bot, is_unique, session_id, destination_url, metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (link_id, self.click_id, self.now, "smoke", "SmokeBrowser/1.0", "https://instagram.com", "US", "desktop", 0, 0, 1, self.marker, f"https://viltrox.com/products/{self.marker}", _json({"marker": self.marker})),
        )
        conn.commit()
        return kol_id, project_id, link_id

    def run(self) -> dict[str, Any]:
        self.cleanup()
        kol_id, project_id, link_id = self.seed()
        order_payload = {
            "id": int(self.order_numeric_id),
            "admin_graphql_api_id": self.order_gid,
            "name": f"#{self.marker}",
            "order_number": self.order_numeric_id[-6:],
            "processed_at": self.now,
            "created_at": self.now,
            "currency": "USD",
            "current_subtotal_price": "1299.00",
            "current_total_price": "1299.00",
            "financial_status": "paid",
            "fulfillment_status": "fulfilled",
            "landing_site": f"https://viltrox.com/products/{self.marker}?vkpi_click_id={self.click_id}&utm_campaign={self.marker}&utm_source=instagram",
            "note_attributes": [{"name": "vkpi_click_id", "value": self.click_id}],
            "discount_codes": [],
            "line_items": [{"sku": self.sku, "title": "Smoke Lens", "quantity": 1, "price": "1299.00"}],
        }
        first = self.post("/api/vkpi/webhooks/shopify/orders", order_payload, {"X-Shopify-Topic": "orders/create", "X-Shopify-Order-Id": self.order_numeric_id})
        dupe = self.post("/api/vkpi/webhooks/shopify/orders", order_payload, {"X-Shopify-Topic": "orders/create", "X-Shopify-Order-Id": self.order_numeric_id})
        snapshot = dict(self.conn.execute("SELECT * FROM vkpi_shopify_order_snapshots WHERE shopify_order_id=?", (self.order_gid,)).fetchone())
        attrs = [dict(row) for row in self.conn.execute("SELECT * FROM vkpi_sales_attributions WHERE source_platform='shopify' AND source_ref=?", (f"shopify:{self.order_gid}",)).fetchall()]
        assert len(attrs) == 1, f"order attribution count expected 1 got {len(attrs)}"
        assert int(attrs[0]["revenue_cents"]) == 129900
        assert int(attrs[0]["project_id"]) == project_id
        assert int(attrs[0]["link_id"]) == link_id
        assert int(attrs[0]["shopify_order_snapshot_id"]) == int(snapshot["id"])
        assert first["shopify_order_snapshot_id"] == snapshot["id"] and dupe["shopify_order_snapshot_id"] == snapshot["id"]
        detail = self.get(f"/api/admin/vkpi/shopify/orders/{snapshot['id']}")
        assert int(detail["order_snapshot"]["id"]) == int(snapshot["id"])
        assert detail["source_row_count"] == 1
        assert int(detail["attributions"][0]["project_id"]) == project_id
        sensitive_order_views = int(self.conn.execute("SELECT COUNT(*) AS n FROM vkpi_sensitive_access_logs WHERE action_type='view_shopify_order' AND metadata_json LIKE ?", (f"%{self.marker}%",)).fetchone()["n"])
        business_order_views = int(self.conn.execute("SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE action_type='shopify_order_view' AND metadata_json LIKE ?", (f"%{self.marker}%",)).fetchone()["n"])
        assert sensitive_order_views >= 1 and business_order_views >= 1, {
            "sensitive_order_views": sensitive_order_views,
            "business_order_views": business_order_views,
        }
        confirmed = self.get("/api/admin/vkpi/attribution/confirmed?source=shopify&limit=20")
        assert any(int(row.get("id") or 0) == int(attrs[0]["id"]) for row in confirmed.get("attributions", []))
        sync = self.admin_post("/api/admin/vkpi/shopify/sync", {"marker": self.marker})
        backfill = self.admin_post("/api/admin/vkpi/shopify/backfill", {"marker": self.marker})
        assert sync["provider_status"] in {"configured", "not_configured"}
        assert backfill["provider_status"] in {"configured", "not_configured"}

        dashboard = self.get("/api/admin/vkpi/dashboard?window_days=7")
        gmv_metric = next((item for item in dashboard.get("metrics", []) if item.get("metric_key") == "gmv"), None)
        assert gmv_metric and int(gmv_metric.get("metric_value_id") or 0), gmv_metric
        drilldown = self.get(f"/api/admin/vkpi/lineage/values/{int(gmv_metric['metric_value_id'])}/drilldown")
        rows = drilldown.get("rows") or drilldown.get("sources") or []
        matched_rows = [row for row in rows if self.marker in _json(row)]
        assert matched_rows, "GMV drilldown did not include smoke Shopify source row"
        assert "shopify_order" in _json(matched_rows[0]) or "order_snapshot" in _json(matched_rows[0])

        refund_payload = {
            "id": self.refund_gid,
            "admin_graphql_api_id": self.refund_gid,
            "order_id": int(self.order_numeric_id),
            "created_at": self.now,
            "transactions": [{"kind": "refund", "amount": "99.00"}],
            "note": self.marker,
        }
        refund_first = self.post("/api/vkpi/webhooks/shopify/refunds", refund_payload, {"X-Shopify-Topic": "refunds/create"})
        refund_dupe = self.post("/api/vkpi/webhooks/shopify/refunds", refund_payload, {"X-Shopify-Topic": "refunds/create"})
        refund_source_ref = f"shopify:refund:{self.refund_gid}"
        refund_rows = [dict(row) for row in self.conn.execute("SELECT * FROM vkpi_sales_attributions WHERE source_platform='shopify' AND source_ref=?", (refund_source_ref,)).fetchall()]
        assert len(refund_rows) == 1, f"refund attribution count expected 1 got {len(refund_rows)}"
        refund_row = refund_rows[0]
        assert int(refund_row["revenue_cents"]) == -9900
        assert int(refund_row["project_id"]) == project_id
        assert int(refund_row["kol_id"]) == kol_id
        assert int(refund_row["staff_id"]) == self.staff_id
        assert int(refund_row["shopify_order_snapshot_id"]) == int(snapshot["id"])
        status = self.conn.execute("SELECT refund_status FROM vkpi_shopify_order_snapshots WHERE id=?", (snapshot["id"],)).fetchone()["refund_status"]
        assert status == "refunded", status
        adjust_count = int(self.conn.execute("SELECT COUNT(*) AS n FROM vkpi_attribution_adjustments WHERE attribution_id=?", (refund_row["id"],)).fetchone()["n"])
        assert adjust_count == 1, f"duplicate refund should not duplicate adjustment logs, got {adjust_count}"
        assert refund_first.get("matched") is True, refund_first
        assert refund_dupe.get("idempotent") is True or refund_dupe.get("attribution", {}).get("id") == refund_row["id"], refund_dupe
        net = int(self.conn.execute("SELECT COALESCE(SUM(revenue_cents),0) AS n FROM vkpi_sales_attributions WHERE evidence_json LIKE ? OR product_sku=?", (f"%{self.marker}%", self.sku)).fetchone()["n"])
        assert net == 120000, f"net revenue expected 120000 got {net}"
        cleanup_counts = self.cleanup()
        assert all(value == 0 for key, value in cleanup_counts.items() if key != "metric_runs_deleted"), cleanup_counts
        return {
            "marker": self.marker,
            "order_auth_mode": first.get("auth_mode"),
            "order_matched": first.get("matched"),
            "snapshot_id": snapshot["id"],
            "order_attribution_id": attrs[0]["id"],
            "order_detail_source_rows": detail["source_row_count"],
            "shopify_sync_status": sync["status"],
            "shopify_backfill_status": backfill["status"],
            "refund_attribution_id": refund_row["id"],
            "refund_matched": refund_first.get("matched"),
            "refund_duplicate_idempotent": refund_dupe.get("idempotent"),
            "net_revenue_cents_before_cleanup": net,
            "cleanup": cleanup_counts,
        }


def main() -> None:
    ensure_vkpi_schema()
    ensure_vkpi_lineage_schema()
    ensure_vkpi_reconciliation_schema()
    smoke = Smoke()
    try:
        result = smoke.run()
    finally:
        # 任一中途断言/HTTP 异常也必须清掉真实本地库里的 smoke 行，避免假 GMV 留存。
        smoke.cleanup()
    stdout_out(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
