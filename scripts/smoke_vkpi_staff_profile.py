#!/usr/bin/env python3
"""Smoke test for V-KPI staff profile drilldown and scope.

The profile is the manager-side entry point from staff leaderboard / staff list
into one employee's real projects, KOL claims, links, sales, costs, KPI ledger,
channels, and audit rows. This smoke keeps all records under a unique marker and
removes them before exit.
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
from app.services.vkpi.schema_channels import ensure_vkpi_channels_schema

BASE = "http://127.0.0.1:8102"
PREFIX = "vkpi-staff-profile-smoke-"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Smoke:
    def __init__(self) -> None:
        self.marker = f"{PREFIX}{int(time.time())}"
        self.now = _now()
        self.day = self.now[:10]
        self.conn = get_conn()
        self.manager_user_id = 0
        self.manager_staff_id = 0
        self.manager_token = ""
        self.employee_user_id = 0
        self.employee_staff_id = 0
        self.employee_token = ""
        self.other_user_id = 0
        self.other_staff_id = 0
        self.other_token = ""
        self.sku = f"{self.marker}-sku"
        self.slug = f"{self.marker}-slug"

    def request(self, method: str, path: str, token: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if payload is None else _json(payload).encode("utf-8")
        req = urllib.request.Request(BASE + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"HTTP {exc.code} {method} {path}: {body[:500]}") from exc

    def request_status(self, method: str, path: str, token: str) -> tuple[int, str]:
        req = urllib.request.Request(BASE + path, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return int(resp.status), resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return int(exc.code), exc.read().decode("utf-8", errors="ignore")

    def cleanup(self, marker: str | None = None) -> dict[str, int]:
        marker = marker or self.marker
        like = f"%{marker}%"
        conn = self.conn

        user_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchall()]
        staff_ids = [int(row["id"]) for row in conn.execute(
            "SELECT s.id FROM staff s LEFT JOIN users u ON u.id=s.user_id WHERE u.email LIKE ? OR u.name LIKE ?",
            (like, like),
        ).fetchall()]
        project_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchall()]
        kol_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?", (like, like, like)).fetchall()]
        link_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_links WHERE slug LIKE ? OR link_uid LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchall()]

        def delete_in(table: str, column: str, ids: list[int]) -> None:
            if not ids:
                return
            ph = ",".join("?" for _ in ids)
            conn.execute(f"DELETE FROM {table} WHERE {column} IN ({ph})", ids)

        delete_in("vkpi_channel_metrics", "channel_id", [int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_employee_channels WHERE channel_uid LIKE ? OR metadata_json LIKE ?", (like, like)).fetchall()])
        delete_in("vkpi_channel_audit", "staff_id", staff_ids)
        conn.execute("DELETE FROM vkpi_employee_channels WHERE channel_uid LIKE ? OR metadata_json LIKE ?", (like, like))
        delete_in("vkpi_kpi_ledger", "staff_id", staff_ids)
        conn.execute("DELETE FROM vkpi_kpi_ledger WHERE source_ref LIKE ? OR metadata_json LIKE ?", (like, like))
        conn.execute("DELETE FROM vkpi_business_audit_logs WHERE metadata_json LIKE ? OR detail LIKE ?", (like, like))
        delete_in("vkpi_business_audit_logs", "staff_id", staff_ids)
        delete_in("vkpi_sensitive_access_logs", "staff_id", staff_ids)
        if staff_ids:
            ph = ",".join("?" for _ in staff_ids)
            conn.execute(f"DELETE FROM vkpi_sensitive_access_logs WHERE resource_type='staff' AND resource_id IN ({ph})", [str(v) for v in staff_ids])
            conn.execute(f"DELETE FROM vkpi_business_audit_logs WHERE target_type='staff' AND target_id IN ({ph})", [str(v) for v in staff_ids])
        conn.execute("DELETE FROM vkpi_sales_attributions WHERE source_ref LIKE ? OR evidence_json LIKE ? OR product_sku LIKE ?", (like, like, like))
        conn.execute("DELETE FROM vkpi_cost_ledger WHERE source_ref LIKE ? OR note LIKE ? OR metadata_json LIKE ?", (like, like, like))
        delete_in("vkpi_link_clicks", "link_id", link_ids)
        conn.execute("DELETE FROM vkpi_link_clicks WHERE event_id LIKE ? OR metadata_json LIKE ?", (like, like))
        delete_in("vkpi_links", "id", link_ids)
        delete_in("vkpi_project_stage_events", "project_id", project_ids)
        delete_in("vkpi_kol_claims", "kol_id", kol_ids)
        delete_in("vkpi_projects", "id", project_ids)
        delete_in("kols", "id", kol_ids)
        delete_in("staff", "id", staff_ids)
        delete_in("users", "id", user_ids)
        conn.commit()

        return {
            "users": int(conn.execute("SELECT COUNT(*) AS n FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchone()["n"]),
            "staff": int(conn.execute(
                "SELECT COUNT(*) AS n FROM staff s LEFT JOIN users u ON u.id=s.user_id WHERE u.email LIKE ? OR u.name LIKE ?",
                (like, like),
            ).fetchone()["n"]),
            "kols": int(conn.execute("SELECT COUNT(*) AS n FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?", (like, like, like)).fetchone()["n"]),
            "projects": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "links": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_links WHERE slug LIKE ? OR link_uid LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "attributions": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_sales_attributions WHERE source_ref LIKE ? OR evidence_json LIKE ? OR product_sku LIKE ?", (like, like, like)).fetchone()["n"]),
            "costs": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_cost_ledger WHERE source_ref LIKE ? OR note LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "kpi_ledger": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_kpi_ledger WHERE source_ref LIKE ? OR metadata_json LIKE ?", (like, like)).fetchone()["n"]),
            "channels": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_employee_channels WHERE channel_uid LIKE ? OR metadata_json LIKE ?", (like, like)).fetchone()["n"]),
            "audit": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE metadata_json LIKE ? OR detail LIKE ?", (like, like)).fetchone()["n"]),
            "sensitive": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_sensitive_access_logs WHERE metadata_json LIKE ? OR page_path LIKE ?", (like, like)).fetchone()["n"]),
        }

    def seed_identity(self, suffix: str, role: str, permissions: str) -> tuple[int, int, str]:
        email = f"{self.marker}-{suffix}@example.com"
        self.conn.execute(
            "INSERT INTO users (created_at, email, password_hash, name, status, role, email_verified, avatar_url, creator_code) VALUES (?,?,?,?,?,?,?,?,?)",
            (self.now, email, "v2:00:00", f"{self.marker}-{suffix}", "approved", role, 1, f"/uploads/staff_avatars/{self.marker}-{suffix}.png", f"{self.marker}-{suffix}"),
        )
        user_id = int(self.conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"])
        self.conn.execute(
            "INSERT INTO staff (user_id, role, permissions_json, active, invited_at, accepted_at, last_active_at) VALUES (?,?,?,?,?,?,?)",
            (user_id, role, _json({"vkpi": permissions}), 1, self.now, self.now, self.now),
        )
        staff_id = int(self.conn.execute("SELECT id FROM staff WHERE user_id=?", (user_id,)).fetchone()["id"])
        self.conn.commit()
        return user_id, staff_id, make_token(user_id, role)

    def seed(self) -> dict[str, int]:
        self.manager_user_id, self.manager_staff_id, self.manager_token = self.seed_identity("manager", "admin", "write")
        self.employee_user_id, self.employee_staff_id, self.employee_token = self.seed_identity("employee", "operator", "write")
        self.other_user_id, self.other_staff_id, self.other_token = self.seed_identity("other", "operator", "write")

        conn = self.conn
        conn.execute(
            "INSERT INTO kols (channel_name, channel_url, platform, contact_email, avatar_url, follower_count, avg_views, assigned_staff_id, created_by_staff_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (self.marker, f"https://instagram.com/{self.marker}", "instagram", f"{self.marker}@creator.test", f"https://cdn.example/{self.marker}.jpg", 50000, 12000, self.employee_staff_id, self.employee_staff_id, self.now, self.now),
        )
        kol_id = int(conn.execute("SELECT id FROM kols WHERE channel_name=?", (self.marker,)).fetchone()["id"])
        conn.execute(
            "INSERT INTO vkpi_kol_claims (kol_id, staff_id, status, claimed_at, last_effective_touch_at, metadata_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (kol_id, self.employee_staff_id, "active", self.now, self.now, _json({"marker": self.marker}), self.now, self.now),
        )
        conn.execute(
            """
            INSERT INTO vkpi_projects (
                project_uid, project_name, kol_id, assigned_staff_id, created_by_staff_id,
                product_sku, product_name, platform, stage, stage_status, started_at,
                last_activity_at, metadata_json, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (self.marker, f"{self.marker} staff project", kol_id, self.employee_staff_id, self.employee_staff_id, self.sku, "Smoke Lens", "instagram", "published", "active", self.now, self.now, _json({"marker": self.marker}), self.now, self.now),
        )
        project_id = int(conn.execute("SELECT id FROM vkpi_projects WHERE project_uid=?", (self.marker,)).fetchone()["id"])
        conn.execute(
            "INSERT INTO vkpi_links (link_uid, slug, link_type, destination_url, platform, product_sku, campaign_name, kol_id, project_id, staff_id, created_by_staff_id, status, redirect_mode, allowlist_status, bot_filter_mode, click_count, valid_click_count, bot_click_count, metadata_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.marker, self.slug, "shopify", f"https://viltrox.com/products/{self.marker}", "shopify", self.sku, self.marker, kol_id, project_id, self.employee_staff_id, self.employee_staff_id, "active", 302, "allowed", "standard", 4, 3, 1, _json({"marker": self.marker}), self.now, self.now),
        )
        link_id = int(conn.execute("SELECT id FROM vkpi_links WHERE slug=?", (self.slug,)).fetchone()["id"])
        conn.execute(
            "INSERT INTO vkpi_sales_attributions (source_platform, source_ref, project_id, link_id, kol_id, staff_id, product_sku, revenue_cents, commission_cents, currency, attribution_model, confidence, occurred_at, imported_at, evidence_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("shopify", f"{self.marker}-order", project_id, link_id, kol_id, self.employee_staff_id, self.sku, 129900, 0, "USD", "click", "confirmed", self.now, self.now, _json({"marker": self.marker}), self.now),
        )
        conn.execute(
            "INSERT INTO vkpi_cost_ledger (project_id, kol_id, staff_id, cost_type, amount_cents, currency, status, incurred_at, source_ref, note, created_by_staff_id, metadata_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, kol_id, self.employee_staff_id, "shipping", 39900, "USD", "actual", self.now, self.marker, self.marker, self.manager_staff_id, _json({"marker": self.marker}), self.now),
        )
        conn.execute(
            "INSERT INTO vkpi_kpi_ledger (ledger_date, staff_id, kol_id, project_id, metric_key, metric_value, source_type, source_ref, confidence, metadata_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (self.day, self.employee_staff_id, kol_id, project_id, "workload_score", 88, "smoke", self.marker, "confirmed", _json({"marker": self.marker}), self.now),
        )
        conn.execute(
            "INSERT INTO vkpi_kpi_ledger (ledger_date, staff_id, kol_id, project_id, metric_key, metric_value, source_type, source_ref, confidence, metadata_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (self.day, self.employee_staff_id, kol_id, project_id, "recommendation_project_created", 1, "recommendation_outcome", f"{self.marker}-rec-project", "confirmed", _json({"marker": self.marker, "recommendation_id": 901, "outcome_id": 902, "launch_id": 903, "kol_pool_id": 904}), self.now),
        )
        conn.execute(
            "INSERT INTO vkpi_kpi_ledger (ledger_date, staff_id, kol_id, project_id, metric_key, metric_value, source_type, source_ref, confidence, metadata_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (self.day, self.employee_staff_id, kol_id, project_id, "recommendation_gmv_cents", 129900, "recommendation_outcome", f"{self.marker}-rec-gmv", "confirmed", _json({"marker": self.marker, "recommendation_id": 901, "outcome_id": 902, "launch_id": 903, "kol_pool_id": 904}), self.now),
        )
        conn.execute(
            "INSERT INTO vkpi_employee_channels (channel_uid, staff_id, platform, account_handle, account_display_name, account_url, avatar_url, auth_method, self_reported_followers, self_reported_posts, status, last_sync_at, last_sync_status, metadata_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.marker, self.employee_staff_id, "instagram", self.marker, f"{self.marker} IG", f"https://instagram.com/{self.marker}", f"https://cdn.example/{self.marker}.jpg", "manual_api_key", 50000, 25, "active", self.now, "ok", _json({"marker": self.marker}), self.now, self.now),
        )
        conn.execute(
            "INSERT INTO vkpi_business_audit_logs (staff_id, action_type, target_type, target_id, detail, metadata_json, created_at) VALUES (?,?,?,?,?,?,?)",
            (self.employee_staff_id, "project_stage_change", "project", str(project_id), self.marker, _json({"marker": self.marker}), self.now),
        )
        conn.commit()
        return {"kol_id": kol_id, "project_id": project_id, "link_id": link_id}

    def run(self) -> dict[str, Any]:
        self.cleanup()
        try:
            seed = self.seed()
            manager = self.request("GET", f"/api/marketing/staff/{self.employee_staff_id}/profile?window=month&limit=120", self.manager_token)
            employee = self.request("GET", f"/api/marketing/staff/{self.employee_staff_id}/profile?window=month&limit=120", self.employee_token)
            other_status, other_body = self.request_status("GET", f"/api/marketing/staff/{self.employee_staff_id}/profile?window=month", self.other_token)

            assert manager.get("staff", {}).get("staff_id") == self.employee_staff_id, manager.get("staff")
            assert len(manager.get("projects") or []) >= 1, manager
            assert len(manager.get("claims") or []) >= 1, manager
            assert len(manager.get("links") or []) >= 1, manager
            assert len(manager.get("attributions") or []) >= 1, manager
            assert len(manager.get("costs") or []) >= 1, manager
            assert len(manager.get("kpi_ledger") or []) >= 1, manager
            manager_breakdown = manager.get("kpi_breakdown") or {}
            assert len(manager_breakdown.get("grouped") or []) >= 2, manager_breakdown
            assert len(manager_breakdown.get("source_rows") or []) >= 3, manager_breakdown
            assert len(manager_breakdown.get("recommendation_grouped") or []) >= 2, manager_breakdown
            assert len(manager_breakdown.get("recommendation_source_rows") or []) >= 2, manager_breakdown
            assert any((row.get("evidence") or {}).get("recommendation_id") == 901 for row in manager_breakdown.get("recommendation_source_rows") or []), manager_breakdown
            assert len(manager.get("channels") or []) >= 1, manager
            assert len(manager.get("audit_events") or []) >= 1, manager
            assert manager.get("visibility", {}).get("costs_visible") is True, manager.get("visibility")
            assert manager.get("visibility", {}).get("audit_visible") is True, manager.get("visibility")

            assert employee.get("staff", {}).get("staff_id") == self.employee_staff_id, employee.get("staff")
            assert len(employee.get("projects") or []) >= 1, employee
            assert len(employee.get("claims") or []) >= 1, employee
            assert len(employee.get("links") or []) >= 1, employee
            assert len(employee.get("attributions") or []) >= 1, employee
            assert employee.get("costs") == [], employee.get("costs")
            assert len(employee.get("kpi_ledger") or []) >= 1, employee
            employee_breakdown = employee.get("kpi_breakdown") or {}
            assert len(employee_breakdown.get("recommendation_source_rows") or []) >= 2, employee_breakdown
            assert len(employee.get("channels") or []) >= 1, employee
            assert employee.get("audit_events") == [], employee.get("audit_events")
            assert employee.get("visibility", {}).get("costs_visible") is False, employee.get("visibility")
            assert employee.get("visibility", {}).get("audit_visible") is False, employee.get("visibility")

            assert other_status == 403, {"status": other_status, "body": other_body[:300]}
            sensitive_count = int(self.conn.execute(
                "SELECT COUNT(*) AS n FROM vkpi_sensitive_access_logs WHERE action_type='view_staff_profile' AND resource_type='staff' AND resource_id=?",
                (str(self.employee_staff_id),),
            ).fetchone()["n"])
            business_count = int(self.conn.execute(
                "SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE action_type='staff_profile_view' AND target_type='staff' AND target_id=?",
                (str(self.employee_staff_id),),
            ).fetchone()["n"])
            assert sensitive_count >= 2, sensitive_count
            assert business_count >= 2, business_count

            cleanup_counts = self.cleanup()
            assert all(value == 0 for value in cleanup_counts.values()), cleanup_counts
            return {
                "marker": self.marker,
                "seed": {
                    **seed,
                    "manager_staff_id": self.manager_staff_id,
                    "employee_staff_id": self.employee_staff_id,
                    "other_staff_id": self.other_staff_id,
                },
                "manager_counts": {
                    "projects": len(manager.get("projects") or []),
                    "claims": len(manager.get("claims") or []),
                    "links": len(manager.get("links") or []),
                    "attributions": len(manager.get("attributions") or []),
                    "costs": len(manager.get("costs") or []),
                    "kpi_ledger": len(manager.get("kpi_ledger") or []),
                    "kpi_grouped": len(manager_breakdown.get("grouped") or []),
                    "recommendation_kpi_sources": len(manager_breakdown.get("recommendation_source_rows") or []),
                    "channels": len(manager.get("channels") or []),
                    "audit_events": len(manager.get("audit_events") or []),
                },
                "employee_costs_hidden": employee.get("visibility", {}).get("costs_visible") is False,
                "other_employee_blocked": other_status,
                "audit": {"sensitive": sensitive_count, "business": business_count},
                "cleanup": cleanup_counts,
            }
        except Exception:
            self.cleanup()
            raise


def main() -> None:
    ensure_vkpi_schema()
    ensure_vkpi_audit_schema()
    ensure_vkpi_channels_schema()
    smoke = Smoke()
    result = smoke.run()
    stdout_out(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
