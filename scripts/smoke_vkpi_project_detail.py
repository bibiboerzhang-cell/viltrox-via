#!/usr/bin/env python3
"""Smoke test for V-KPI Project Detail evidence chain.

Creates a temporary KOL/project/link/click, captures message/content/terms/
shipment/cost rows through the local API where possible, ingests a Shopify
order, verifies GET /projects/{id} returns the complete detail drawer payload,
and deletes every row containing the smoke marker.
"""
from __future__ import annotations

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
from app.services.vkpi.schema_audit import ensure_vkpi_audit_schema
from app.services.vkpi.schema_lineage import ensure_vkpi_lineage_schema
from app.services.vkpi.schema_reconciliation import ensure_vkpi_reconciliation_schema
from app.services.vkpi.schema_reports import ensure_vkpi_reports_schema

BASE = "http://127.0.0.1:8102"
PREFIX = "vkpi-project-detail-smoke-"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Smoke:
    def __init__(self) -> None:
        self.marker = f"{PREFIX}{int(time.time())}"
        self.now = _now()
        self.conn = get_conn()
        self.user_id: int | None = None
        self.staff_id: int | None = None
        self.token = ""
        self.order_numeric_id = str(int(time.time() * 1000))
        self.order_gid = f"gid://shopify/Order/{self.order_numeric_id}"
        self.click_id = f"{self.marker}-click"
        self.slug = f"{self.marker}-slug"
        self.sku = f"{self.marker}-sku"

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None, *, auth: bool = True, headers: dict[str, str] | None = None) -> dict[str, Any]:
        data = None if payload is None else _json(payload).encode("utf-8")
        req = urllib.request.Request(BASE + path, data=data, method=method)
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        if auth:
            req.add_header("Authorization", f"Bearer {self.token}")
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        if not auth and PLATFORM_INGEST_SHARED_SECRET:
            req.add_header("X-Viltrox-Ingest-Secret", PLATFORM_INGEST_SHARED_SECRET)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"HTTP {exc.code} {method} {path}: {body[:500]}") from exc

    def cleanup(self, marker: str | None = None) -> dict[str, int]:
        marker = marker or self.marker
        conn = self.conn
        like = f"%{marker}%"

        source_rows = conn.execute(
            "SELECT metric_value_id FROM vkpi_metric_sources WHERE snapshot_json LIKE ? OR evidence_ref LIKE ?",
            (like, like),
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

        project_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchall()]
        kol_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?", (like, like, like)).fetchall()]
        link_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_links WHERE slug LIKE ? OR link_uid LIKE ? OR campaign_name LIKE ? OR metadata_json LIKE ?", (like, like, like, like)).fetchall()]
        post_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_content_posts WHERE post_url LIKE ? OR title LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchall()]
        attr_ids = [
            int(row["id"])
            for row in conn.execute(
                """
                SELECT id FROM vkpi_sales_attributions
                WHERE source_ref LIKE ? OR evidence_json LIKE ? OR product_sku LIKE ?
                   OR shopify_order_snapshot_id IN (SELECT id FROM vkpi_shopify_order_snapshots WHERE raw_payload_json LIKE ? OR order_name LIKE ?)
                """,
                (like, like, like, like, like),
            ).fetchall()
        ]

        def delete_in(table: str, column: str, ids: list[int]) -> None:
            if not ids:
                return
            ph = ",".join("?" for _ in ids)
            conn.execute(f"DELETE FROM {table} WHERE {column} IN ({ph})", ids)

        delete_in("vkpi_attribution_adjustments", "attribution_id", attr_ids)
        delete_in("vkpi_sales_attributions", "id", attr_ids)
        conn.execute("DELETE FROM vkpi_reconciliation_queue WHERE source_ref LIKE ? OR raw_payload_json LIKE ?", (like, like))
        delete_in("vkpi_link_clicks", "link_id", link_ids)
        conn.execute("DELETE FROM vkpi_link_clicks WHERE event_id LIKE ? OR metadata_json LIKE ?", (like, like))
        delete_in("vkpi_links", "id", link_ids)
        delete_in("vkpi_message_attachments", "message_id", [int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_messages WHERE body LIKE ? OR snippet LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchall()])
        delete_in("vkpi_messages", "project_id", project_ids)
        conn.execute("DELETE FROM vkpi_messages WHERE body LIKE ? OR snippet LIKE ? OR metadata_json LIKE ?", (like, like, like))
        delete_in("vkpi_content_assets", "post_id", post_ids)
        delete_in("vkpi_content_assets", "project_id", project_ids)
        delete_in("vkpi_content_posts", "id", post_ids)
        delete_in("vkpi_content_posts", "project_id", project_ids)
        delete_in("vkpi_project_deliverables", "project_id", project_ids)
        delete_in("vkpi_project_terms", "project_id", project_ids)
        delete_in("vkpi_shipments", "project_id", project_ids)
        delete_in("vkpi_sample_assets", "project_id", project_ids)
        delete_in("vkpi_cost_ledger", "project_id", project_ids)
        conn.execute("DELETE FROM vkpi_cost_ledger WHERE source_ref LIKE ? OR note LIKE ? OR metadata_json LIKE ?", (like, like, like))
        delete_in("vkpi_project_stage_events", "project_id", project_ids)
        delete_in("vkpi_projects", "id", project_ids)
        delete_in("vkpi_kol_claims", "kol_id", kol_ids)
        delete_in("kols", "id", kol_ids)
        conn.execute("DELETE FROM vkpi_shopify_order_snapshots WHERE raw_payload_json LIKE ? OR order_name LIKE ? OR landing_site LIKE ?", (like, like, like))
        conn.execute("DELETE FROM vkpi_sensitive_access_logs WHERE metadata_json LIKE ? OR page_path LIKE ? OR resource_id LIKE ?", (like, like, like))
        conn.execute("DELETE FROM vkpi_export_logs WHERE filters_json LIKE ? OR ip LIKE ?", (like, like))
        conn.execute("DELETE FROM vkpi_business_audit_logs WHERE detail LIKE ? OR metadata_json LIKE ? OR target_id LIKE ?", (like, like, like))
        conn.execute("DELETE FROM vkpi_export_jobs WHERE export_uid LIKE ? OR filters_json LIKE ? OR file_path LIKE ?", (like, like, like))
        conn.execute("DELETE FROM vkpi_report_files WHERE file_path LIKE ?", (like,))
        conn.execute("DELETE FROM vkpi_report_runs WHERE report_uid LIKE ? OR metadata_json LIKE ?", (like, like))
        user_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchall()]
        if user_ids:
            ph = ",".join("?" for _ in user_ids)
            conn.execute(f"DELETE FROM staff WHERE user_id IN ({ph})", user_ids)
            conn.execute(f"DELETE FROM users WHERE id IN ({ph})", user_ids)
        conn.commit()

        counts = {
            "metric_sources": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_metric_sources WHERE snapshot_json LIKE ? OR evidence_ref LIKE ?", (like, like)).fetchone()["n"]),
            "attributions": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_sales_attributions WHERE source_ref LIKE ? OR evidence_json LIKE ? OR product_sku LIKE ?", (like, like, like)).fetchone()["n"]),
            "snapshots": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_shopify_order_snapshots WHERE raw_payload_json LIKE ? OR order_name LIKE ? OR landing_site LIKE ?", (like, like, like)).fetchone()["n"]),
            "projects": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "kols": int(conn.execute("SELECT COUNT(*) AS n FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?", (like, like, like)).fetchone()["n"]),
            "links": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_links WHERE slug LIKE ? OR link_uid LIKE ? OR campaign_name LIKE ? OR metadata_json LIKE ?", (like, like, like, like)).fetchone()["n"]),
            "messages": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_messages WHERE body LIKE ? OR snippet LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "content_posts": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_content_posts WHERE post_url LIKE ? OR title LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "content_assets": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_content_assets WHERE asset_url LIKE ? OR metadata_json LIKE ?", (like, like)).fetchone()["n"]),
            "costs": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_cost_ledger WHERE source_ref LIKE ? OR note LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "business_audit": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE detail LIKE ? OR metadata_json LIKE ? OR target_id LIKE ?", (like, like, like)).fetchone()["n"]),
            "users": int(conn.execute("SELECT COUNT(*) AS n FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchone()["n"]),
            "metric_runs_deleted": len(run_ids),
        }
        return counts

    def seed_actor(self) -> tuple[int, int]:
        email = f"{self.marker}@example.com"
        self.conn.execute(
            "INSERT INTO users (created_at, email, password_hash, name, status, role, email_verified) VALUES (?,?,?,?,?,?,?)",
            (self.now, email, "v2:00:00", self.marker, "approved", "admin", 1),
        )
        user_id = int(self.conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"])
        staff_cols = {str(row["name"]) for row in self.conn.execute("PRAGMA table_info(staff)").fetchall()}
        insert_cols = ["user_id", "role", "permissions_json", "active", "invited_at"]
        values: list[Any] = [user_id, "admin", _json({"vkpi": "admin"}), 1, self.now]
        if "is_owner" in staff_cols:
            insert_cols.append("is_owner")
            values.append(1)
        if "email_domain_verified" in staff_cols:
            insert_cols.append("email_domain_verified")
            values.append(1)
        self.conn.execute(
            f"INSERT INTO staff ({', '.join(insert_cols)}) VALUES ({','.join('?' for _ in insert_cols)})",
            values,
        )
        staff_id = int(self.conn.execute("SELECT id FROM staff WHERE user_id=?", (user_id,)).fetchone()["id"])
        self.conn.commit()
        return user_id, staff_id

    def seed_base(self) -> tuple[int, int, int]:
        conn = self.conn
        assert self.staff_id is not None
        conn.execute(
            "INSERT INTO kols (channel_name, channel_url, platform, contact_email, assigned_staff_id, created_by_staff_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (self.marker, f"https://instagram.com/{self.marker}", "instagram", f"{self.marker}@example.com", self.staff_id, self.staff_id, self.now, self.now),
        )
        kol_id = int(conn.execute("SELECT id FROM kols WHERE channel_name=?", (self.marker,)).fetchone()["id"])
        conn.execute(
            """
            INSERT INTO vkpi_projects (
                project_uid, project_name, kol_id, assigned_staff_id, created_by_staff_id,
                product_sku, product_name, platform, stage, stage_status, started_at,
                last_activity_at, metadata_json, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self.marker,
                f"{self.marker} Project Detail",
                kol_id,
                self.staff_id,
                self.staff_id,
                self.sku,
                "Smoke Lens 35mm F1.2",
                "instagram",
                "published",
                "active",
                self.now,
                self.now,
                _json({"marker": self.marker}),
                self.now,
                self.now,
            ),
        )
        project_id = int(conn.execute("SELECT id FROM vkpi_projects WHERE project_uid=?", (self.marker,)).fetchone()["id"])
        conn.execute(
            "INSERT INTO vkpi_project_stage_events (project_id, from_stage, to_stage, event_type, actor_staff_id, note, source_ref_type, source_ref_id, effective_at, metadata_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, "agreed", "published", "publish", self.staff_id, self.marker, "smoke", self.marker, self.now, _json({"marker": self.marker}), self.now),
        )
        conn.execute(
            """
            INSERT INTO vkpi_links (
                link_uid, slug, link_type, destination_url, platform, product_sku, campaign_name,
                kol_id, project_id, staff_id, created_by_staff_id, status, redirect_mode,
                allowlist_status, bot_filter_mode, utm_source, utm_medium, utm_campaign,
                utm_content, click_count, valid_click_count, bot_click_count, metadata_json,
                created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self.marker,
                self.slug,
                "shopify",
                f"https://viltrox.com/products/{self.marker}",
                "shopify",
                self.sku,
                self.marker,
                kol_id,
                project_id,
                self.staff_id,
                self.staff_id,
                "active",
                302,
                "allowed",
                "standard",
                "vkpi",
                "kol",
                self.marker,
                "smoke",
                1,
                1,
                0,
                _json({"marker": self.marker}),
                self.now,
                self.now,
            ),
        )
        link_id = int(conn.execute("SELECT id FROM vkpi_links WHERE slug=?", (self.slug,)).fetchone()["id"])
        conn.execute(
            "INSERT INTO vkpi_link_clicks (link_id, event_id, clicked_at, ip_hash, user_agent, referrer, country_code, device_type, bot_score, is_bot, is_unique, session_id, destination_url, metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (link_id, self.click_id, self.now, "smoke", "SmokeBrowser/1.0", "https://instagram.com", "US", "desktop", 0, 0, 1, self.marker, f"https://viltrox.com/products/{self.marker}", _json({"marker": self.marker})),
        )
        conn.commit()
        return kol_id, project_id, link_id

    def ingest_order(self) -> dict[str, Any]:
        payload = {
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
        return self.request("POST", "/api/vkpi/webhooks/shopify/orders", payload, auth=False, headers={"X-Shopify-Topic": "orders/create", "X-Shopify-Order-Id": self.order_numeric_id})

    def run(self) -> dict[str, Any]:
        self.cleanup()
        self.user_id, self.staff_id = self.seed_actor()
        self.token = make_token(self.user_id, "admin")
        kol_id, project_id, link_id = self.seed_base()
        try:
            message = self.request(
                "POST",
                f"/api/admin/vkpi/projects/{project_id}/messages",
                {
                    "source": "manual",
                    "direction": "outbound",
                    "sender": "Jianbo",
                    "receiver": self.marker,
                    "body": f"{self.marker} message evidence",
                    "snippet": "Smoke follow-up evidence",
                    "evidence_url": f"https://evidence.example/{self.marker}/message",
                    "metadata": {"marker": self.marker},
                },
            )
            content = self.request(
                "POST",
                f"/api/admin/vkpi/projects/{project_id}/content",
                {
                    "post_url": f"https://instagram.com/p/{self.marker}",
                    "title": f"{self.marker} published video",
                    "thumbnail_url": f"https://cdn.example/{self.marker}.jpg",
                    "published_at": self.now,
                    "content_type": "video",
                    "views": 12345,
                    "likes": 456,
                    "comments": 78,
                    "shares": 9,
                    "rights_status": "approved",
                    "ad_usage_allowed": True,
                    "metadata": {"marker": self.marker},
                },
            )
            post_id = int(content.get("id") or 0)
            assert post_id, content
            self.conn.execute(
                "INSERT INTO vkpi_content_assets (post_id, project_id, asset_url, asset_type, usage_rights, metadata_json, created_at) VALUES (?,?,?,?,?,?,?)",
                (post_id, project_id, f"https://cdn.example/{self.marker}/asset.mp4", "video", "approved", _json({"marker": self.marker}), self.now),
            )
            self.conn.commit()
            terms = self.request(
                "POST",
                f"/api/admin/vkpi/projects/{project_id}/terms",
                {
                    "cash_fee_usd": 100,
                    "sample_terms": "ship sample after agreement",
                    "usage_rights": "organic repost allowed",
                    "due_at": self.now,
                    "note": self.marker,
                    "deliverables": [
                        {"deliverable_type": "video", "quantity": 1, "status": "planned", "due_at": self.now, "note": self.marker},
                    ],
                },
            )
            shipment = self.request(
                "POST",
                f"/api/admin/vkpi/projects/{project_id}/shipments",
                {
                    "product_sku": self.sku,
                    "product_name": "Smoke Lens 35mm F1.2",
                    "serial_number": f"SN-{self.marker}",
                    "sample_cost_usd": 299,
                    "shipping_cost_usd": 35,
                    "carrier": "DHL",
                    "tracking_number": f"TRK-{self.marker}",
                    "evidence_url": f"https://evidence.example/{self.marker}/shipment",
                    "metadata": {"marker": self.marker},
                },
            )
            cost = self.request(
                "POST",
                f"/api/admin/vkpi/projects/{project_id}/costs",
                {"cost_type": "cash_fee", "amount_usd": 100, "source_ref": f"fee:{self.marker}", "note": self.marker, "metadata": {"marker": self.marker}},
            )
            order_result = self.ingest_order()
            detail = self.request("GET", f"/api/admin/vkpi/projects/{project_id}")

            assert detail.get("project", {}).get("id") == project_id, detail.get("project")
            assert any(self.marker in _json(row) for row in detail.get("events", [])), "stage event missing"
            assert any(row.get("id") == link_id for row in detail.get("links", [])), "link missing"
            assert any(row.get("link_id") == link_id and row.get("event_id") == self.click_id for row in detail.get("link_clicks", [])), "link click evidence missing"
            link_summary = detail.get("link_summary") or {}
            assert int(link_summary.get("valid_click_count") or 0) >= 1, link_summary
            assert any(self.marker in _json(row) for row in detail.get("messages", [])), "message missing"
            assert any(self.marker in _json(row) and int(row.get("views") or 0) == 12345 for row in detail.get("content_posts", [])), "content post missing"
            assert any(self.marker in _json(row) for row in detail.get("content_assets", [])), "content asset missing"
            assert detail.get("terms", {}).get("sample_terms") == "ship sample after agreement", detail.get("terms")
            assert detail.get("deliverables") and detail["deliverables"][0].get("source") == "terms.deliverables_json", detail.get("deliverables")
            assert any(self.marker in _json(row) for row in detail.get("shipments", [])), "shipment missing"
            assert any(self.marker in _json(row) for row in detail.get("samples", [])), "sample missing"
            assert any(self.marker in _json(row) for row in detail.get("costs", [])), "cost missing"
            sales = detail.get("sales_attributions") or []
            assert sales, "sales attribution missing"
            assert any(row.get("order_snapshot") and self.marker in _json(row.get("order_snapshot")) for row in sales), sales
            link_orders = detail.get("link_orders") or []
            assert any(row.get("shopify_order_snapshot_id") and self.marker in _json(row) for row in link_orders), link_orders
            audit_events = detail.get("audit_events") or []
            assert any(self.marker in _json(row) for row in audit_events), "project audit missing"
            roi = detail.get("roi") or {}
            assert int(roi.get("revenue_cents") or 0) == 129900, roi
            assert int(roi.get("cost_cents") or 0) >= 10000, roi
            assert float(roi.get("roi") or 0) > 0, roi

            cleanup_counts = self.cleanup()
            assert all(value == 0 for key, value in cleanup_counts.items() if key != "metric_runs_deleted"), cleanup_counts
            return {
                "marker": self.marker,
                "project_id": project_id,
                "kol_id": kol_id,
                "link_id": link_id,
                "message_id": message.get("id"),
                "content_post_id": content.get("id"),
                "terms_id": terms.get("id"),
                "shipment_id": shipment.get("id"),
                "cost_id": (cost.get("cost") or {}).get("id"),
                "shopify_order_snapshot_id": order_result.get("shopify_order_snapshot_id"),
                "detail_counts": {
                    "events": len(detail.get("events") or []),
                    "links": len(detail.get("links") or []),
                    "link_clicks": len(detail.get("link_clicks") or []),
                    "link_orders": len(detail.get("link_orders") or []),
                    "messages": len(detail.get("messages") or []),
                    "content_posts": len(detail.get("content_posts") or []),
                    "content_assets": len(detail.get("content_assets") or []),
                    "deliverables": len(detail.get("deliverables") or []),
                    "shipments": len(detail.get("shipments") or []),
                    "samples": len(detail.get("samples") or []),
                    "costs": len(detail.get("costs") or []),
                    "sales_attributions": len(sales),
                    "audit_events": len(audit_events),
                },
                "roi": roi,
                "cleanup": cleanup_counts,
            }
        except Exception:
            self.cleanup()
            raise


def main() -> None:
    ensure_vkpi_schema()
    ensure_vkpi_lineage_schema()
    ensure_vkpi_reconciliation_schema()
    ensure_vkpi_reports_schema()
    ensure_vkpi_audit_schema()
    smoke = Smoke()
    result = smoke.run()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
