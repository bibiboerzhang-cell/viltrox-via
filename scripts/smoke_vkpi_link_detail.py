#!/usr/bin/env python3
"""Smoke test for V-KPI Link Center detail/click/order/archive flow."""
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

from app.core.security import make_token
from app.db.connection import get_conn
from app.services.vkpi.schema import ensure_vkpi_schema

BASE = "http://127.0.0.1:8102"
PREFIX = "vkpi-link-detail-smoke-"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Smoke:
    def __init__(self) -> None:
        self.marker = f"{PREFIX}{int(time.time())}"
        self.now = _now()
        self.conn = get_conn()
        self.user_id = 0
        self.staff_id = 0
        self.token = ""
        self.slug = f"{self.marker}-slug"
        self.sku = f"{self.marker}-sku"
        self.order_gid = f"gid://shopify/Order/{int(time.time() * 1000)}"

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if payload is None else _json(payload).encode("utf-8")
        req = urllib.request.Request(BASE + path, data=data, method=method)
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self.token}")
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
        attr_ids = [
            int(row["id"])
            for row in conn.execute(
                """
                SELECT id FROM vkpi_sales_attributions
                WHERE source_ref LIKE ? OR evidence_json LIKE ? OR product_sku LIKE ?
                   OR shopify_order_snapshot_id IN (
                       SELECT id FROM vkpi_shopify_order_snapshots WHERE raw_payload_json LIKE ? OR order_name LIKE ?
                   )
                """,
                (like, like, like, like, like),
            ).fetchall()
        ]
        if attr_ids:
            ph = ",".join("?" for _ in attr_ids)
            conn.execute(f"DELETE FROM vkpi_attribution_adjustments WHERE attribution_id IN ({ph})", attr_ids)
            conn.execute(f"DELETE FROM vkpi_sales_attributions WHERE id IN ({ph})", attr_ids)
        conn.execute("DELETE FROM vkpi_link_clicks WHERE event_id LIKE ? OR metadata_json LIKE ? OR session_id LIKE ?", (like, like, like))
        conn.execute("DELETE FROM vkpi_links WHERE slug LIKE ? OR link_uid LIKE ? OR campaign_name LIKE ? OR metadata_json LIKE ?", (like, like, like, like))
        conn.execute("DELETE FROM vkpi_project_stage_events WHERE project_id IN (SELECT id FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?)", (like, like, like))
        conn.execute("DELETE FROM vkpi_cost_ledger WHERE project_id IN (SELECT id FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?)", (like, like, like))
        conn.execute("DELETE FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?", (like, like, like))
        conn.execute("DELETE FROM vkpi_kol_claims WHERE kol_id IN (SELECT id FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?)", (like, like, like))
        conn.execute("DELETE FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?", (like, like, like))
        conn.execute("DELETE FROM vkpi_shopify_order_snapshots WHERE raw_payload_json LIKE ? OR order_name LIKE ? OR landing_site LIKE ?", (like, like, like))
        conn.execute("DELETE FROM vkpi_business_audit_logs WHERE detail LIKE ? OR metadata_json LIKE ? OR target_id LIKE ?", (like, like, like))
        user_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchall()]
        if user_ids:
            ph = ",".join("?" for _ in user_ids)
            conn.execute(f"DELETE FROM staff WHERE user_id IN ({ph})", user_ids)
            conn.execute(f"DELETE FROM users WHERE id IN ({ph})", user_ids)
        conn.commit()
        return {
            "users": int(conn.execute("SELECT COUNT(*) AS n FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchone()["n"]),
            "attributions": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_sales_attributions WHERE source_ref LIKE ? OR evidence_json LIKE ? OR product_sku LIKE ?", (like, like, like)).fetchone()["n"]),
            "snapshots": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_shopify_order_snapshots WHERE raw_payload_json LIKE ? OR order_name LIKE ? OR landing_site LIKE ?", (like, like, like)).fetchone()["n"]),
            "clicks": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_link_clicks WHERE event_id LIKE ? OR metadata_json LIKE ? OR session_id LIKE ?", (like, like, like)).fetchone()["n"]),
            "links": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_links WHERE slug LIKE ? OR link_uid LIKE ? OR campaign_name LIKE ? OR metadata_json LIKE ?", (like, like, like, like)).fetchone()["n"]),
            "projects": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "kols": int(conn.execute("SELECT COUNT(*) AS n FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?", (like, like, like)).fetchone()["n"]),
            "business_audit": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE detail LIKE ? OR metadata_json LIKE ? OR target_id LIKE ?", (like, like, like)).fetchone()["n"]),
        }

    def seed_actor(self) -> None:
        conn = self.conn
        email = f"{self.marker}-admin@example.com"
        conn.execute(
            "INSERT INTO users (created_at, email, password_hash, name, status, role, email_verified) VALUES (?,?,?,?,?,?,?)",
            (self.now, email, "v2:00:00", f"{self.marker}-admin", "approved", "admin", 1),
        )
        self.user_id = int(conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"])
        staff_cols = {str(row["name"]) for row in conn.execute("PRAGMA table_info(staff)").fetchall()}
        insert_cols = ["user_id", "role", "permissions_json", "mfa_enabled", "active", "invited_by", "invited_at"]
        values: list[Any] = [self.user_id, "admin", _json({"vkpi": "write"}), 0, 1, None, self.now]
        if "is_owner" in staff_cols:
            insert_cols.append("is_owner")
            values.append(1)
        if "email_domain_verified" in staff_cols:
            insert_cols.append("email_domain_verified")
            values.append(1)
        conn.execute(f"INSERT INTO staff ({', '.join(insert_cols)}) VALUES ({','.join('?' for _ in insert_cols)})", values)
        self.staff_id = int(conn.execute("SELECT id FROM staff WHERE user_id=?", (self.user_id,)).fetchone()["id"])
        conn.commit()
        self.token = make_token(self.user_id, "admin")

    def seed(self) -> tuple[int, int, int]:
        conn = self.conn
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
                f"{self.marker} Link Detail",
                kol_id,
                self.staff_id,
                self.staff_id,
                self.sku,
                "Smoke Link Lens",
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
            """
            INSERT INTO vkpi_links (
                link_uid, slug, link_type, destination_url, platform, product_sku,
                campaign_name, kol_id, project_id, staff_id, created_by_staff_id,
                status, redirect_mode, allowlist_status, bot_filter_mode, utm_source,
                utm_medium, utm_campaign, utm_content, click_count, valid_click_count,
                bot_click_count, metadata_json, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self.marker,
                self.slug,
                "shopify",
                f"https://viltrox.com/products/{self.marker}",
                "instagram",
                self.sku,
                self.marker,
                kol_id,
                project_id,
                self.staff_id,
                self.staff_id,
                "live",
                302,
                "allowed",
                "standard",
                "instagram",
                "kol",
                self.marker,
                "smoke",
                2,
                1,
                1,
                _json({"marker": self.marker}),
                self.now,
                self.now,
            ),
        )
        link_id = int(conn.execute("SELECT id FROM vkpi_links WHERE slug=?", (self.slug,)).fetchone()["id"])
        conn.execute(
            "INSERT INTO vkpi_link_clicks (link_id, event_id, clicked_at, ip_hash, user_agent, referrer, country_code, device_type, bot_score, is_bot, is_unique, session_id, destination_url, metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (link_id, f"{self.marker}-valid-click", self.now, "smoke", "SmokeBrowser/1.0", "https://instagram.com", "US", "mobile", 0, 0, 1, self.marker, f"https://viltrox.com/products/{self.marker}", _json({"marker": self.marker, "kind": "valid"})),
        )
        conn.execute(
            "INSERT INTO vkpi_link_clicks (link_id, event_id, clicked_at, ip_hash, user_agent, referrer, country_code, device_type, bot_score, is_bot, is_unique, session_id, destination_url, metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (link_id, f"{self.marker}-bot-click", self.now, "smoke", "GoogleBot/1.0", "https://preview.example", "US", "desktop", 95, 1, 0, self.marker, f"https://viltrox.com/products/{self.marker}", _json({"marker": self.marker, "kind": "bot"})),
        )
        conn.execute(
            """
            INSERT INTO vkpi_shopify_order_snapshots (
                shopify_order_id, admin_graphql_api_id, order_name, order_number,
                processed_at, currency, subtotal_cents, total_cents, financial_status,
                fulfillment_status, refund_status, landing_site, raw_payload_json,
                created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self.order_gid,
                self.order_gid,
                f"#{self.marker}",
                self.marker[-8:],
                self.now,
                "USD",
                129900,
                129900,
                "paid",
                "fulfilled",
                "",
                f"https://viltrox.com/products/{self.marker}?vkpi_click_id={self.marker}-valid-click",
                _json({"marker": self.marker}),
                self.now,
                self.now,
            ),
        )
        snapshot_id = int(conn.execute("SELECT id FROM vkpi_shopify_order_snapshots WHERE shopify_order_id=?", (self.order_gid,)).fetchone()["id"])
        conn.execute(
            """
            INSERT INTO vkpi_sales_attributions (
                source_platform, source_ref, project_id, link_id, kol_id, staff_id,
                shopify_order_snapshot_id, product_sku, revenue_cents, commission_cents,
                currency, confidence, occurred_at, imported_at, evidence_json, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "shopify",
                f"shopify:{self.marker}",
                project_id,
                link_id,
                kol_id,
                self.staff_id,
                snapshot_id,
                self.sku,
                129900,
                0,
                "USD",
                "confirmed",
                self.now,
                self.now,
                _json({"marker": self.marker, "source": "smoke"}),
                self.now,
            ),
        )
        conn.commit()
        return kol_id, project_id, link_id

    def run(self) -> dict[str, Any]:
        self.cleanup()
        self.seed_actor()
        _, _, link_id = self.seed()
        detail = self.request("GET", f"/api/admin/vkpi/links/{link_id}")
        clicks = self.request("GET", f"/api/admin/vkpi/links/{link_id}/clicks?limit=10")
        orders = self.request("GET", f"/api/admin/vkpi/links/{link_id}/orders?limit=10")
        health = self.request("POST", f"/api/admin/vkpi/links/{link_id}/health-check", {})
        archived = self.request("POST", f"/api/admin/vkpi/links/{link_id}/archive", {})
        detail_after = self.request("GET", f"/api/admin/vkpi/links/{link_id}")

        assert int(detail["summary"]["click_count"]) == 2, detail
        assert int(detail["summary"]["valid_click_count"]) == 1, detail
        assert int(detail["summary"]["bot_click_count"]) == 1, detail
        assert int(detail["summary"]["orders"]) == 1, detail
        assert int(detail["summary"]["revenue_cents"]) == 129900, detail
        assert len(clicks["clicks"]) == 2, clicks
        assert int(clicks["summary"]["valid_click_count"]) == 1, clicks
        assert int(clicks["summary"]["bot_click_count"]) == 1, clicks
        assert len(orders["orders"]) == 1, orders
        assert int(orders["summary"]["revenue_cents"]) == 129900, orders
        assert health["ok"] is True, health
        assert archived["status"] == "archived", archived
        db_status = self.conn.execute("SELECT status FROM vkpi_links WHERE id=?", (link_id,)).fetchone()["status"]
        assert db_status == "archived", db_status
        audit_events = detail_after.get("audit_events") or []
        audit_blob = _json(audit_events)
        assert self.marker in audit_blob, audit_events
        assert "link_health_check" in audit_blob, audit_events
        assert "link_archive" in audit_blob, audit_events

        cleanup_counts = self.cleanup()
        assert all(value == 0 for value in cleanup_counts.values()), cleanup_counts
        return {
            "marker": self.marker,
            "link_id": link_id,
            "detail_summary": detail["summary"],
            "clicks_summary": clicks["summary"],
            "orders_summary": orders["summary"],
            "archived_status": archived["status"],
            "audit_events": len(audit_events),
            "cleanup": cleanup_counts,
        }


def main() -> None:
    ensure_vkpi_schema()
    smoke = Smoke()
    result = smoke.run()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
