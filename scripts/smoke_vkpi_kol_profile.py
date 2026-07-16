#!/usr/bin/env python3
"""Smoke test for V-KPI KOL Profile evidence aggregation and scope.

Creates a temporary employee-owned KOL, project evidence, Shopify snapshot,
sales attribution, and internal cost. Verifies manager sees the full KOL Profile
with Shopify evidence while employee scope hides internal cost data. Cleans all
rows containing the smoke marker.
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

from app.core.security import make_token
from app.db.connection import get_conn
from app.services.vkpi.schema import ensure_vkpi_schema
from app.services.vkpi.schema_audit import ensure_vkpi_audit_schema
from app.domains.lineage import ensure_vkpi_lineage_schema
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema
from app.services.vkpi.schema_reconciliation import ensure_vkpi_reconciliation_schema
from app.domains.reports import ensure_vkpi_reports_schema

BASE = "http://127.0.0.1:8102"
PREFIX = "vkpi-kol-profile-smoke-"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Smoke:
    def __init__(self) -> None:
        self.marker = f"{PREFIX}{int(time.time())}"
        self.now = _now()
        self.conn = get_conn()
        self.admin_token = ""
        self.admin_user_id = 0
        self.admin_staff_id = 0
        self.employee_user_id = 0
        self.employee_staff_id = 0
        self.employee_token = ""
        self.order_gid = f"gid://shopify/Order/{int(time.time() * 1000)}"
        self.sku = f"{self.marker}-sku"

    def get(self, path: str, token: str) -> dict[str, Any]:
        req = urllib.request.Request(BASE + path, method="GET")
        req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"HTTP {exc.code} GET {path}: {body[:500]}") from exc

    def cleanup(self, marker: str | None = None) -> dict[str, int]:
        marker = marker or self.marker
        like = f"%{marker}%"
        conn = self.conn
        project_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchall()]
        kol_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?", (like, like, like)).fetchall()]
        link_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_links WHERE project_id IN (SELECT id FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?) OR slug LIKE ? OR link_uid LIKE ? OR metadata_json LIKE ?", (like, like, like, like, like, like)).fetchall()]
        user_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchall()]
        attr_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_sales_attributions WHERE source_ref LIKE ? OR evidence_json LIKE ? OR product_sku LIKE ?", (like, like, like)).fetchall()]
        recommendation_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_kol_recommendations WHERE recommendation_uid LIKE ? OR feature_snapshot_json LIKE ? OR scoring_breakdown_json LIKE ?", (like, like, like)).fetchall()]
        kol_pool_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_kol_pool WHERE pool_uid LIKE ? OR handle LIKE ? OR source_ref LIKE ?", (like, like, like)).fetchall()]
        launch_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_product_launches WHERE launch_uid LIKE ? OR name LIKE ? OR product_sku LIKE ?", (like, like, like)).fetchall()]

        def delete_in(table: str, column: str, ids: list[int]) -> None:
            if not ids:
                return
            ph = ",".join("?" for _ in ids)
            conn.execute(f"DELETE FROM {table} WHERE {column} IN ({ph})", ids)

        delete_in("vkpi_attribution_adjustments", "attribution_id", attr_ids)
        delete_in("vkpi_recommendation_assignments", "recommendation_id", recommendation_ids)
        delete_in("vkpi_recommendation_feedback", "recommendation_id", recommendation_ids)
        delete_in("vkpi_recommendation_explanations", "recommendation_id", recommendation_ids)
        delete_in("vkpi_recommendation_outcomes", "recommendation_id", recommendation_ids)
        delete_in("vkpi_kol_recommendations", "id", recommendation_ids)
        conn.execute("DELETE FROM vkpi_kol_recommendation_runs WHERE run_uid LIKE ? OR filters_json LIKE ?", (like, like))
        delete_in("vkpi_kol_pool_aliases", "kol_pool_id", kol_pool_ids)
        delete_in("vkpi_kol_pool_brand_links", "kol_pool_id", kol_pool_ids)
        delete_in("vkpi_kol_embeddings", "kol_pool_id", kol_pool_ids)
        delete_in("vkpi_kol_pool", "id", kol_pool_ids)
        delete_in("vkpi_product_launches", "id", launch_ids)
        delete_in("vkpi_link_clicks", "link_id", link_ids)
        conn.execute("DELETE FROM vkpi_link_clicks WHERE event_id LIKE ? OR metadata_json LIKE ? OR session_id LIKE ?", (like, like, like))
        delete_in("vkpi_sales_attributions", "id", attr_ids)
        conn.execute("DELETE FROM vkpi_reconciliation_queue WHERE source_ref LIKE ? OR raw_payload_json LIKE ?", (like, like))
        conn.execute("DELETE FROM vkpi_business_audit_logs WHERE detail LIKE ? OR metadata_json LIKE ? OR target_id LIKE ?", (like, like, like))
        conn.execute("DELETE FROM vkpi_kpi_ledger WHERE source_ref LIKE ? OR metadata_json LIKE ?", (like, like))
        delete_in("vkpi_content_assets", "project_id", project_ids)
        delete_in("vkpi_content_posts", "project_id", project_ids)
        delete_in("vkpi_messages", "project_id", project_ids)
        delete_in("vkpi_cost_ledger", "project_id", project_ids)
        delete_in("vkpi_project_stage_events", "project_id", project_ids)
        delete_in("vkpi_links", "project_id", project_ids)
        delete_in("vkpi_kol_claims", "kol_id", kol_ids)
        delete_in("vkpi_projects", "id", project_ids)
        delete_in("kols", "id", kol_ids)
        conn.execute("DELETE FROM vkpi_shopify_order_snapshots WHERE raw_payload_json LIKE ? OR order_name LIKE ? OR landing_site LIKE ?", (like, like, like))
        delete_in("staff", "user_id", user_ids)
        delete_in("users", "id", user_ids)
        conn.commit()
        return {
            "users": int(conn.execute("SELECT COUNT(*) AS n FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchone()["n"]),
            "projects": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "kols": int(conn.execute("SELECT COUNT(*) AS n FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?", (like, like, like)).fetchone()["n"]),
            "attributions": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_sales_attributions WHERE source_ref LIKE ? OR evidence_json LIKE ? OR product_sku LIKE ?", (like, like, like)).fetchone()["n"]),
            "snapshots": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_shopify_order_snapshots WHERE raw_payload_json LIKE ? OR order_name LIKE ? OR landing_site LIKE ?", (like, like, like)).fetchone()["n"]),
            "messages": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_messages WHERE body LIKE ? OR snippet LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "content": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_content_posts WHERE post_url LIKE ? OR title LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "costs": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_cost_ledger WHERE source_ref LIKE ? OR note LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "clicks": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_link_clicks WHERE event_id LIKE ? OR metadata_json LIKE ? OR session_id LIKE ?", (like, like, like)).fetchone()["n"]),
            "business_audit": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE detail LIKE ? OR metadata_json LIKE ? OR target_id LIKE ?", (like, like, like)).fetchone()["n"]),
            "kpi_ledger": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_kpi_ledger WHERE source_ref LIKE ? OR metadata_json LIKE ?", (like, like)).fetchone()["n"]),
            "recommendations": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_recommendations WHERE recommendation_uid LIKE ? OR feature_snapshot_json LIKE ? OR scoring_breakdown_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "recommendation_outcomes": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_recommendation_outcomes WHERE content_url LIKE ? OR feature_snapshot_json LIKE ? OR scoring_breakdown_json LIKE ?", (like, like, like)).fetchone()["n"]),
        }

    def create_actor(self, suffix: str, role: str, permission: str, *, is_owner: int = 0) -> tuple[int, int, str]:
        conn = self.conn
        email = f"{self.marker}-{suffix}@example.com"
        conn.execute(
            "INSERT INTO users (created_at, email, password_hash, name, status, role, email_verified, avatar_url) VALUES (?,?,?,?,?,?,?,?)",
            (self.now, email, "v2:00:00", f"{self.marker}-{suffix}", "approved", role, 1, f"/uploads/{self.marker}-{suffix}.png"),
        )
        user_id = int(conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"])
        staff_cols = {str(row["name"]) for row in conn.execute("PRAGMA table_info(staff)").fetchall()}
        insert_cols = ["user_id", "role", "permissions_json", "mfa_enabled", "active", "invited_by", "invited_at"]
        values: list[Any] = [user_id, role, _json({"vkpi": permission}), 0, 1, None, self.now]
        if "is_owner" in staff_cols:
            insert_cols.append("is_owner")
            values.append(is_owner)
        if "email_domain_verified" in staff_cols:
            insert_cols.append("email_domain_verified")
            values.append(1)
        conn.execute(f"INSERT INTO staff ({', '.join(insert_cols)}) VALUES ({','.join('?' for _ in insert_cols)})", values)
        staff_id = int(conn.execute("SELECT id FROM staff WHERE user_id=?", (user_id,)).fetchone()["id"])
        conn.commit()
        return user_id, staff_id, make_token(user_id, role)

    def seed(self) -> tuple[int, int, int]:
        conn = self.conn
        self.admin_user_id, self.admin_staff_id, self.admin_token = self.create_actor("admin", "admin", "write", is_owner=1)
        self.employee_user_id, self.employee_staff_id, self.employee_token = self.create_actor("employee", "employee", "write")
        conn.execute(
            "INSERT INTO kols (channel_name, channel_url, platform, contact_email, avatar_url, profile_url, contact_links_json, contact_raw_json, follower_count, avg_views, assigned_staff_id, created_by_staff_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                self.marker,
                f"https://instagram.com/{self.marker}",
                "ig",
                f"contact-{self.marker}@creator.test",
                f"https://cdn.example/{self.marker}.jpg",
                f"https://instagram.com/{self.marker}",
                _json([{"label": "Media Kit", "value": "media", "url": f"https://media.example/{self.marker}"}]),
                _json({"source": "smoke", "marker": self.marker}),
                123456,
                23456,
                self.employee_staff_id,
                self.employee_staff_id,
                self.now,
                self.now,
            ),
        )
        kol_id = int(conn.execute("SELECT id FROM kols WHERE channel_name=?", (self.marker,)).fetchone()["id"])
        conn.execute(
            "INSERT INTO vkpi_kol_claims (kol_id, staff_id, status, claimed_at, last_effective_touch_at, metadata_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (kol_id, self.employee_staff_id, "active", self.now, self.now, _json({"marker": self.marker}), self.now, self.now),
        )
        conn.execute(
            "INSERT INTO vkpi_projects (project_uid, project_name, kol_id, assigned_staff_id, created_by_staff_id, product_sku, product_name, platform, stage, stage_status, started_at, last_activity_at, metadata_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.marker, f"{self.marker} profile project", kol_id, self.employee_staff_id, self.employee_staff_id, self.sku, "Smoke Lens", "instagram", "published", "active", self.now, self.now, _json({"marker": self.marker}), self.now, self.now),
        )
        project_id = int(conn.execute("SELECT id FROM vkpi_projects WHERE project_uid=?", (self.marker,)).fetchone()["id"])
        conn.execute(
            "INSERT INTO vkpi_links (link_uid, slug, link_type, destination_url, platform, product_sku, campaign_name, kol_id, project_id, staff_id, created_by_staff_id, status, redirect_mode, allowlist_status, bot_filter_mode, click_count, valid_click_count, bot_click_count, metadata_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.marker, f"{self.marker}-slug", "shopify", f"https://viltrox.com/products/{self.marker}", "shopify", self.sku, self.marker, kol_id, project_id, self.employee_staff_id, self.employee_staff_id, "active", 302, "allowed", "standard", 10, 9, 1, _json({"marker": self.marker}), self.now, self.now),
        )
        link_id = int(conn.execute("SELECT id FROM vkpi_links WHERE link_uid=?", (self.marker,)).fetchone()["id"])
        conn.execute(
            "INSERT INTO vkpi_link_clicks (link_id, event_id, clicked_at, ip_hash, user_agent, referrer, country_code, device_type, bot_score, is_bot, is_unique, session_id, destination_url, metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (link_id, f"{self.marker}-click", self.now, self.marker, "SmokeAgent", f"https://instagram.com/{self.marker}", "US", "mobile", 0, 0, 1, f"{self.marker}-session", f"https://viltrox.com/products/{self.marker}", _json({"marker": self.marker})),
        )
        snapshot_payload = {"marker": self.marker, "id": self.order_gid, "landing_site": f"https://viltrox.com/{self.marker}"}
        conn.execute(
            "INSERT INTO vkpi_shopify_order_snapshots (shopify_order_id, admin_graphql_api_id, order_name, order_number, processed_at, currency, subtotal_cents, total_cents, financial_status, fulfillment_status, refund_status, discount_codes_json, landing_site, note_attributes_json, line_items_json, raw_payload_hash, raw_payload_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.order_gid, self.order_gid, f"#{self.marker}", "1001", self.now, "USD", 129900, 129900, "paid", "fulfilled", "", "[]", f"https://viltrox.com/{self.marker}", "{}", _json([{"sku": self.sku}]), self.marker, _json(snapshot_payload), self.now, self.now),
        )
        snapshot_id = int(conn.execute("SELECT id FROM vkpi_shopify_order_snapshots WHERE shopify_order_id=?", (self.order_gid,)).fetchone()["id"])
        conn.execute(
            "INSERT INTO vkpi_sales_attributions (source_platform, source_ref, project_id, link_id, kol_id, staff_id, shopify_order_snapshot_id, product_sku, revenue_cents, currency, confidence, occurred_at, imported_at, evidence_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("shopify", f"shopify:{self.marker}", project_id, link_id, kol_id, self.employee_staff_id, snapshot_id, self.sku, 129900, "USD", "confirmed", self.now, self.now, _json({"marker": self.marker}), self.now),
        )
        attribution_id = int(conn.execute("SELECT id FROM vkpi_sales_attributions WHERE source_ref=?", (f"shopify:{self.marker}",)).fetchone()["id"])
        conn.execute(
            "INSERT INTO vkpi_cost_ledger (project_id, kol_id, staff_id, cost_type, amount_cents, currency, status, incurred_at, source_ref, note, created_by_staff_id, metadata_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, kol_id, self.employee_staff_id, "product", 39900, "USD", "actual", self.now, f"cost:{self.marker}", self.marker, self.admin_staff_id, _json({"marker": self.marker}), self.now),
        )
        conn.execute(
            "INSERT INTO vkpi_business_audit_logs (staff_id, action_type, target_type, target_id, detail, metadata_json, created_at) VALUES (?,?,?,?,?,?,?)",
            (self.employee_staff_id, "kol_profile_smoke", "kol", str(kol_id), self.marker, _json({"marker": self.marker, "attribution_id": attribution_id, "link_id": link_id}), self.now),
        )
        conn.execute(
            "INSERT INTO vkpi_kpi_ledger (ledger_date, staff_id, kol_id, project_id, metric_key, metric_value, source_type, source_ref, confidence, metadata_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (self.now[:10], self.employee_staff_id, kol_id, project_id, "published_count", 1, "project_stage", f"kpi:{self.marker}", "confirmed", _json({"marker": self.marker, "formula": "published stage event"}), self.now),
        )
        conn.execute(
            "INSERT INTO vkpi_product_launches (launch_uid, name, product_sku, product_name, category, target_platforms_json, target_audience_json, status, created_by_staff_id, metadata_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"launch:{self.marker}", f"{self.marker} Launch", self.sku, "Smoke Lens", "lens", _json(["instagram"]), _json({"marker": self.marker}), "active", self.admin_staff_id, _json({"marker": self.marker}), self.now, self.now),
        )
        launch_id = int(conn.execute("SELECT id FROM vkpi_product_launches WHERE launch_uid=?", (f"launch:{self.marker}",)).fetchone()["id"])
        conn.execute(
            "INSERT INTO vkpi_kol_pool (pool_uid, platform, handle, profile_url, display_name, avatar_url, email, followers, avg_views, engagement_rate, viltrox_fit_score, viltrox_fit_reason, linked_main_kol_id, sync_status, source_type, source_ref, raw_platform_data, created_by_staff_id, last_seen_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"pool:{self.marker}", "instagram", self.marker, f"https://instagram.com/{self.marker}", self.marker, f"https://cdn.example/{self.marker}.jpg", f"contact-{self.marker}@creator.test", 123456, 23456, 0.045, 88.0, "smoke fit", kol_id, "imported", "smoke", f"pool:{self.marker}", _json({"marker": self.marker}), self.admin_staff_id, self.now, self.now, self.now),
        )
        kol_pool_id = int(conn.execute("SELECT id FROM vkpi_kol_pool WHERE pool_uid=?", (f"pool:{self.marker}",)).fetchone()["id"])
        conn.execute(
            "INSERT INTO vkpi_kol_recommendation_runs (run_uid, launch_id, strategy_version, status, candidate_count, recommendation_count, filters_json, created_by_staff_id, created_at, completed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"run:{self.marker}", launch_id, "rule_v0", "completed", 1, 1, _json({"marker": self.marker}), self.admin_staff_id, self.now, self.now),
        )
        run_id = int(conn.execute("SELECT id FROM vkpi_kol_recommendation_runs WHERE run_uid=?", (f"run:{self.marker}",)).fetchone()["id"])
        conn.execute(
            "INSERT INTO vkpi_kol_recommendations (recommendation_uid, run_id, launch_id, kol_pool_id, linked_main_kol_id, platform, handle, display_name, score, rank, status, feature_snapshot_json, scoring_breakdown_json, explanation_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"rec:{self.marker}", run_id, launch_id, kol_pool_id, kol_id, "instagram", self.marker, self.marker, 0.88, 1, "recommended", _json({"marker": self.marker, "followers": 123456}), _json({"fit": 88}), _json({"reason": "smoke"}), self.now, self.now),
        )
        recommendation_id = int(conn.execute("SELECT id FROM vkpi_kol_recommendations WHERE recommendation_uid=?", (f"rec:{self.marker}",)).fetchone()["id"])
        conn.execute(
            "INSERT INTO vkpi_recommendation_outcomes (recommendation_id, kol_pool_id, launch_id, was_shortlisted, shortlisted_at, was_claimed, claimed_at, project_created, project_created_at, outreach_sent, outreach_sent_at, reply_received, reply_at, agreement_reached, agreement_at, content_published, content_published_at, content_url, order_attributed, first_order_at, attributed_clicks, attributed_orders, attributed_gmv_cents, attributed_cost_cents, computed_roi, recommended_at, first_action_at, outcome_finalized_at, feature_snapshot_json, scoring_breakdown_json, model_version, display_position, display_context_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (recommendation_id, kol_pool_id, launch_id, True, self.now, True, self.now, True, self.now, True, self.now, True, self.now, True, self.now, True, self.now, f"https://instagram.com/p/{self.marker}", True, self.now, 9, 1, 129900, 39900, 3.2556, self.now, self.now, self.now, _json({"marker": self.marker}), _json({"fit": 88}), "rule_v0", 1, _json({"marker": self.marker, "position": 1})),
        )
        conn.execute(
            "INSERT INTO vkpi_messages (project_id, kol_id, staff_id, source, direction, sender, receiver, body, snippet, evidence_url, captured_at, metadata_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, kol_id, self.employee_staff_id, "manual", "inbound", self.marker, "Jianbo", f"{self.marker} reply", "Smoke reply", f"https://evidence.example/{self.marker}", self.now, _json({"marker": self.marker}), self.now),
        )
        conn.execute(
            "INSERT INTO vkpi_content_posts (project_id, kol_id, platform, post_url, title, thumbnail_url, published_at, content_type, views, likes, comments, shares, rights_status, ad_usage_allowed, metadata_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, kol_id, "instagram", f"https://instagram.com/p/{self.marker}", f"{self.marker} video", f"https://cdn.example/{self.marker}.jpg", self.now, "video", 55555, 888, 77, 12, "approved", True, _json({"marker": self.marker}), self.now, self.now),
        )
        post_id = int(conn.execute("SELECT id FROM vkpi_content_posts WHERE post_url=?", (f"https://instagram.com/p/{self.marker}",)).fetchone()["id"])
        conn.execute(
            "INSERT INTO vkpi_content_assets (post_id, project_id, asset_url, asset_type, usage_rights, metadata_json, created_at) VALUES (?,?,?,?,?,?,?)",
            (post_id, project_id, f"https://cdn.example/{self.marker}/asset.mp4", "video", "approved", _json({"marker": self.marker}), self.now),
        )
        conn.commit()
        return kol_id, project_id, snapshot_id

    def run(self) -> dict[str, Any]:
        self.cleanup()
        kol_id, project_id, snapshot_id = self.seed()
        try:
            manager = self.get(f"/api/marketing/kols/{kol_id}/profile", self.admin_token)
            employee = self.get(f"/api/marketing/kols/{kol_id}/profile", self.employee_token)
            assert manager.get("kol", {}).get("id") == kol_id, manager.get("kol")
            assert manager.get("links") and self.marker in _json(manager.get("links")), "manager links missing"
            assert manager.get("link_clicks") and any(row.get("event_id") == f"{self.marker}-click" for row in manager.get("link_clicks") or []), manager.get("link_clicks")
            link_summary = manager.get("link_summary") or {}
            assert int(link_summary.get("valid_click_count") or 0) >= 1, link_summary
            assert manager.get("link_orders") and any(row.get("shopify_order_snapshot_id") == snapshot_id for row in manager.get("link_orders") or []), manager.get("link_orders")
            assert manager.get("messages") and self.marker in _json(manager.get("messages")), "manager messages missing"
            assert manager.get("content_posts") and self.marker in _json(manager.get("content_posts")), "manager content missing"
            assert manager.get("content_assets") and self.marker in _json(manager.get("content_assets")), "manager content assets missing"
            assert manager.get("audit_events") and self.marker in _json(manager.get("audit_events")), "manager audit missing"
            assert manager.get("kpi_ledger") and any(row.get("source_ref") == f"kpi:{self.marker}" for row in manager.get("kpi_ledger") or []), manager.get("kpi_ledger")
            assert manager.get("kpi_summary") and any(row.get("metric_key") == "published_count" for row in manager.get("kpi_summary") or []), manager.get("kpi_summary")
            assert manager.get("recommendations") and self.marker in _json(manager.get("recommendations")), "manager recommendations missing"
            assert manager.get("recommendation_outcomes") and self.marker in _json(manager.get("recommendation_outcomes")), "manager recommendation outcomes missing"
            assert manager.get("contacts", {}).get("email") == f"contact-{self.marker}@creator.test", manager.get("contacts")
            assert manager.get("sales_attributions") and manager["sales_attributions"][0].get("order_snapshot"), manager.get("sales_attributions")
            assert manager["sales_attributions"][0]["order_snapshot"].get("id") == snapshot_id
            assert manager.get("costs") and int(manager["costs"][0].get("amount_cents") or 0) == 39900, manager.get("costs")
            assert manager.get("summary", {}).get("financials_hidden") is False, manager.get("summary")
            assert int(manager.get("summary", {}).get("message_count") or 0) == 1, manager.get("summary")
            assert employee.get("messages") and self.marker in _json(employee.get("messages")), "employee messages missing"
            assert employee.get("content_posts") and self.marker in _json(employee.get("content_posts")), "employee content missing"
            assert employee.get("kpi_ledger") and self.marker in _json(employee.get("kpi_ledger")), "employee KPI missing"
            assert employee.get("recommendations") == [], employee.get("recommendations")
            assert employee.get("recommendation_outcomes") == [], employee.get("recommendation_outcomes")
            assert employee.get("costs") == [], employee.get("costs")
            assert employee.get("summary", {}).get("financials_hidden") is True, employee.get("summary")
            assert employee.get("summary", {}).get("cost_cents") is None, employee.get("summary")
            cleanup_counts = self.cleanup()
            assert all(value == 0 for value in cleanup_counts.values()), cleanup_counts
            return {
                "marker": self.marker,
                "kol_id": kol_id,
                "project_id": project_id,
                "manager_counts": {
                    "projects": len(manager.get("projects") or []),
                    "links": len(manager.get("links") or []),
                    "link_clicks": len(manager.get("link_clicks") or []),
                    "link_orders": len(manager.get("link_orders") or []),
                    "messages": len(manager.get("messages") or []),
                    "content_posts": len(manager.get("content_posts") or []),
                    "content_assets": len(manager.get("content_assets") or []),
                    "sales_attributions": len(manager.get("sales_attributions") or []),
                    "costs": len(manager.get("costs") or []),
                    "audit_events": len(manager.get("audit_events") or []),
                    "kpi_ledger": len(manager.get("kpi_ledger") or []),
                    "kpi_summary": len(manager.get("kpi_summary") or []),
                    "recommendations": len(manager.get("recommendations") or []),
                    "recommendation_outcomes": len(manager.get("recommendation_outcomes") or []),
                    "timeline": len(manager.get("activity_timeline") or []),
                },
                "employee_financials_hidden": employee.get("summary", {}).get("financials_hidden"),
                "cleanup": cleanup_counts,
            }
        except Exception:
            self.cleanup()
            raise


def main() -> None:
    ensure_vkpi_schema()
    ensure_vkpi_product_industry_schema()
    ensure_vkpi_lineage_schema()
    ensure_vkpi_reconciliation_schema()
    ensure_vkpi_reports_schema()
    ensure_vkpi_audit_schema()
    smoke = Smoke()
    result = smoke.run()
    stdout_out(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
