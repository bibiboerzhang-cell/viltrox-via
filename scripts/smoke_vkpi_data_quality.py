#!/usr/bin/env python3
"""Smoke test for V-KPI Data Quality checks and cleanup.

Seeds temporary broken/ambiguous V-KPI rows, verifies the management data-quality
queue detects them, verifies employees cannot resolve global issues, resolves
one issue, then deletes every row containing the smoke marker.
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

from app.core.security import make_token
from app.db.connection import get_conn
from app.services.vkpi.schema import ensure_vkpi_schema
from app.services.vkpi.schema_audit import ensure_vkpi_audit_schema
from app.domains.lineage import ensure_vkpi_lineage_schema
from app.services.vkpi.schema_reconciliation import ensure_vkpi_reconciliation_schema
from app.domains.reports import ensure_vkpi_reports_schema
from app.domains.data_quality import ensure_data_quality_schema

BASE = "http://127.0.0.1:8102"
PREFIX = "vkpi-data-quality-smoke-"


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
        self.employee_token = ""
        self.employee_user_id = 0
        self.employee_staff_id = 0
        self.order_gid = f"gid://shopify/Order/{int(time.time() * 1000)}"

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None, token: str | None = None, expect_status: int = 200) -> dict[str, Any]:
        data = None if payload is None else _json(payload).encode("utf-8")
        req = urllib.request.Request(BASE + path, data=data, method=method)
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {token or self.admin_token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                if resp.status != expect_status:
                    raise RuntimeError(f"expected HTTP {expect_status}, got {resp.status}: {body[:500]}")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            if exc.code == expect_status:
                return {"status_code": exc.code, "body": body}
            raise RuntimeError(f"HTTP {exc.code} {method} {path}: {body[:500]}") from exc

    def cleanup(self, marker: str | None = None) -> dict[str, int]:
        marker = marker or self.marker
        like = f"%{marker}%"
        conn = self.conn
        project_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchall()]
        kol_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?", (like, like, like)).fetchall()]
        link_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_links WHERE slug LIKE ? OR link_uid LIKE ? OR destination_url LIKE ? OR metadata_json LIKE ?", (like, like, like, like)).fetchall()]
        attr_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_sales_attributions WHERE source_ref LIKE ? OR evidence_json LIKE ? OR product_sku LIKE ?", (like, like, like)).fetchall()]
        user_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchall()]
        metric_run_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_metric_runs WHERE run_uid LIKE ? OR metadata_json LIKE ?", (like, like)).fetchall()]
        report_run_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM vkpi_report_runs WHERE report_uid LIKE ? OR summary_text LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchall()]

        def delete_in(table: str, column: str, ids: list[int]) -> None:
            if not ids:
                return
            ph = ",".join("?" for _ in ids)
            conn.execute(f"DELETE FROM {table} WHERE {column} IN ({ph})", ids)

        delete_in("vkpi_report_files", "report_run_id", report_run_ids)
        delete_in("vkpi_report_runs", "id", report_run_ids)
        if metric_run_ids:
            ph = ",".join("?" for _ in metric_run_ids)
            conn.execute(f"DELETE FROM vkpi_metric_sources WHERE metric_value_id IN (SELECT id FROM vkpi_metric_values WHERE run_id IN ({ph}))", metric_run_ids)
            conn.execute(f"DELETE FROM vkpi_metric_values WHERE run_id IN ({ph})", metric_run_ids)
            conn.execute(f"DELETE FROM vkpi_metric_runs WHERE id IN ({ph})", metric_run_ids)
        delete_in("vkpi_attribution_adjustments", "attribution_id", attr_ids)
        delete_in("vkpi_sales_attributions", "id", attr_ids)
        conn.execute("DELETE FROM vkpi_reconciliation_queue WHERE source_ref LIKE ? OR raw_payload_json LIKE ? OR product_sku LIKE ?", (like, like, like))
        delete_in("vkpi_link_clicks", "link_id", link_ids)
        delete_in("vkpi_links", "id", link_ids)
        delete_in("vkpi_cost_ledger", "project_id", project_ids)
        conn.execute("DELETE FROM vkpi_cost_ledger WHERE source_ref LIKE ? OR note LIKE ? OR metadata_json LIKE ?", (like, like, like))
        conn.execute("DELETE FROM vkpi_kpi_ledger WHERE source_ref LIKE ? OR metadata_json LIKE ?", (like, like))
        delete_in("vkpi_project_stage_events", "project_id", project_ids)
        delete_in("vkpi_projects", "id", project_ids)
        delete_in("vkpi_kol_claims", "kol_id", kol_ids)
        delete_in("kols", "id", kol_ids)
        conn.execute("DELETE FROM vkpi_shopify_order_snapshots WHERE shopify_order_id LIKE ? OR raw_payload_json LIKE ? OR order_name LIKE ? OR landing_site LIKE ?", (like, like, like, like))
        conn.execute("DELETE FROM vkpi_data_quality_actions WHERE issue_id LIKE ? OR reason LIKE ? OR metadata_json LIKE ?", (like, like, like))
        conn.execute("DELETE FROM vkpi_business_audit_logs WHERE target_id LIKE ? OR detail LIKE ? OR metadata_json LIKE ?", (like, like, like))
        delete_in("staff", "user_id", user_ids)
        delete_in("users", "id", user_ids)
        conn.commit()
        return {
            "users": int(conn.execute("SELECT COUNT(*) AS n FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchone()["n"]),
            "projects": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "kols": int(conn.execute("SELECT COUNT(*) AS n FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?", (like, like, like)).fetchone()["n"]),
            "links": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_links WHERE slug LIKE ? OR link_uid LIKE ? OR destination_url LIKE ? OR metadata_json LIKE ?", (like, like, like, like)).fetchone()["n"]),
            "attributions": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_sales_attributions WHERE source_ref LIKE ? OR evidence_json LIKE ? OR product_sku LIKE ?", (like, like, like)).fetchone()["n"]),
            "snapshots": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_shopify_order_snapshots WHERE shopify_order_id LIKE ? OR raw_payload_json LIKE ? OR order_name LIKE ? OR landing_site LIKE ?", (like, like, like, like)).fetchone()["n"]),
            "costs": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_cost_ledger WHERE source_ref LIKE ? OR note LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "queue": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_reconciliation_queue WHERE source_ref LIKE ? OR raw_payload_json LIKE ? OR product_sku LIKE ?", (like, like, like)).fetchone()["n"]),
            "actions": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_data_quality_actions WHERE issue_id LIKE ? OR reason LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "business_audit": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE target_id LIKE ? OR detail LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "metric_runs": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_metric_runs WHERE run_uid LIKE ? OR metadata_json LIKE ?", (like, like)).fetchone()["n"]),
            "metric_values": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_metric_values WHERE calculation_json LIKE ?", (like,)).fetchone()["n"]),
            "kpi_ledger": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_kpi_ledger WHERE source_ref LIKE ? OR metadata_json LIKE ?", (like, like)).fetchone()["n"]),
            "report_runs": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_report_runs WHERE report_uid LIKE ? OR summary_text LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
        }

    def create_actor(self, suffix: str, role: str, permission: str, *, is_owner: int = 0) -> tuple[int, int, str]:
        conn = self.conn
        email = f"{self.marker}-{suffix}@example.com"
        conn.execute(
            "INSERT INTO users (created_at, email, password_hash, name, status, role, email_verified) VALUES (?,?,?,?,?,?,?)",
            (self.now, email, "v2:00:00", f"{self.marker}-{suffix}", "approved", role, 1),
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

    def seed(self) -> dict[str, int]:
        conn = self.conn
        self.admin_user_id, self.admin_staff_id, self.admin_token = self.create_actor("admin", "admin", "write", is_owner=1)
        self.employee_user_id, self.employee_staff_id, self.employee_token = self.create_actor("employee", "employee", "read")

        duplicate_url = f"https://instagram.com/{self.marker}"
        kol_ids: list[int] = []
        for suffix in ("a", "b"):
            conn.execute(
                "INSERT INTO kols (channel_name, channel_url, platform, contact_email, assigned_staff_id, created_by_staff_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (f"{self.marker}-{suffix}", duplicate_url, "instagram", f"{self.marker}@creator.test", self.admin_staff_id, self.admin_staff_id, self.now, self.now),
            )
            kol_ids.append(int(conn.execute("SELECT id FROM kols WHERE channel_name=?", (f"{self.marker}-{suffix}",)).fetchone()["id"]))
        kol_id = kol_ids[0]

        def project(uid_suffix: str, stage: str, status: str = "active") -> int:
            uid = f"{self.marker}-{uid_suffix}"
            conn.execute(
                """
                INSERT INTO vkpi_projects (project_uid, project_name, kol_id, assigned_staff_id, created_by_staff_id, product_sku, product_name, platform, stage, stage_status, started_at, last_activity_at, metadata_json, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (uid, f"{uid} project", kol_id, self.admin_staff_id, self.admin_staff_id, f"{self.marker}-sku", "Smoke Lens", "instagram", stage, status, self.now, self.now, _json({"marker": self.marker}), self.now, self.now),
            )
            return int(conn.execute("SELECT id FROM vkpi_projects WHERE project_uid=?", (uid,)).fetchone()["id"])

        active_project = project("active", "agreed")
        published_project = project("published-no-content", "published")
        shipped_project = project("shipped-no-cost", "shipped")
        deleted_project = project("deleted", "published", "deleted")

        conn.execute(
            """
            INSERT INTO vkpi_links (link_uid, slug, link_type, destination_url, platform, product_sku, campaign_name, kol_id, project_id, staff_id, created_by_staff_id, status, redirect_mode, allowlist_status, bot_filter_mode, health_status, click_count, valid_click_count, bot_click_count, metadata_json, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (self.marker, f"{self.marker}-slug", "shopify", f"https://bad.example/{self.marker}", "shopify", f"{self.marker}-sku", self.marker, kol_id, active_project, self.admin_staff_id, self.admin_staff_id, "active", 302, "blocked", "standard", "broken", 0, 0, 0, _json({"marker": self.marker}), self.now, self.now),
        )
        link_id = int(conn.execute("SELECT id FROM vkpi_links WHERE link_uid=?", (self.marker,)).fetchone()["id"])

        conn.execute(
            "INSERT INTO vkpi_shopify_order_snapshots (shopify_order_id, admin_graphql_api_id, order_name, order_number, processed_at, currency, subtotal_cents, total_cents, financial_status, fulfillment_status, refund_status, discount_codes_json, landing_site, note_attributes_json, line_items_json, raw_payload_hash, raw_payload_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.order_gid, self.order_gid, f"#{self.marker}", "1001", self.now, "USD", 10000, 10000, "paid", "fulfilled", "refunded", "[]", f"https://viltrox.com/{self.marker}", "{}", _json([{"sku": f"{self.marker}-sku"}]), self.marker, _json({"marker": self.marker}), self.now, self.now),
        )
        snapshot_id = int(conn.execute("SELECT id FROM vkpi_shopify_order_snapshots WHERE shopify_order_id=?", (self.order_gid,)).fetchone()["id"])

        attributions = [
            ("shopify", f"shopify:{self.marker}:credit:1", active_project, link_id, kol_id, self.admin_staff_id, snapshot_id, 10000, _json({"marker": self.marker})),
            ("shopify", f"shopify:{self.marker}:credit:2", active_project, link_id, kol_id, self.admin_staff_id, snapshot_id, 5000, _json({"marker": self.marker})),
            ("shopify", f"shopify:{self.marker}:missing-snapshot", active_project, link_id, kol_id, self.admin_staff_id, None, 9000, _json({"marker": self.marker})),
            ("manual", f"manual:{self.marker}:no-evidence", active_project, None, kol_id, self.admin_staff_id, None, 7000, "{}"),
            ("shopify", f"shopify:{self.marker}:unmatched", None, None, None, self.admin_staff_id, None, 8000, _json({"marker": self.marker})),
            ("shopify", f"shopify:{self.marker}:deleted", deleted_project, None, kol_id, self.admin_staff_id, None, 6000, _json({"marker": self.marker})),
        ]
        for source_platform, source_ref, project_id, link_id_value, kol_id_value, staff_id, snapshot, revenue, evidence in attributions:
            conn.execute(
                "INSERT INTO vkpi_sales_attributions (source_platform, source_ref, project_id, link_id, kol_id, staff_id, shopify_order_snapshot_id, product_sku, revenue_cents, currency, confidence, occurred_at, imported_at, evidence_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (source_platform, source_ref, project_id, link_id_value, kol_id_value, staff_id, snapshot, f"{self.marker}-sku", revenue, "USD", "confirmed", self.now, self.now, evidence, self.now),
            )
        stale_amazon_at = "2000-01-01T00:00:00Z"
        conn.execute(
            """
            INSERT INTO vkpi_sales_attributions (
                source_platform, source_ref, project_id, link_id, kol_id, staff_id,
                shopify_order_snapshot_id, product_sku, order_id, amazon_campaign_id,
                revenue_cents, commission_cents, currency, attribution_model, confidence,
                occurred_at, imported_at, evidence_json, created_at
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "amazon",
                f"amazon:{self.marker}:missing-report-fields",
                active_project,
                None,
                kol_id,
                self.admin_staff_id,
                None,
                "",
                None,
                "",
                4500,
                300,
                "USD",
                "amazon_report",
                "confirmed",
                stale_amazon_at,
                stale_amazon_at,
                _json({"marker": self.marker, "normalized": {"asin": "", "campaign": "", "marketplace": "US", "report_date": "2000-01-01"}}),
                self.now,
            ),
        )

        conn.execute(
            "INSERT INTO vkpi_cost_ledger (project_id, kol_id, staff_id, cost_type, amount_cents, currency, status, incurred_at, source_ref, note, created_by_staff_id, metadata_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (deleted_project, kol_id, self.admin_staff_id, "shipping", 1200, "USD", "actual", self.now, f"cost:{self.marker}:deleted", self.marker, self.admin_staff_id, _json({"marker": self.marker}), self.now),
        )
        conn.execute(
            "INSERT INTO vkpi_reconciliation_queue (source_platform, source_ref, order_id, revenue_cents, currency, occurred_at, product_sku, raw_payload_json, status, priority, assigned_to_staff_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("shopify", f"queue:{self.marker}", self.marker, 3000, "USD", self.now, f"{self.marker}-sku", _json({"marker": self.marker}), "pending", 9, self.admin_staff_id, self.now),
        )
        conn.execute(
            "INSERT INTO kols (channel_name, channel_url, platform, contact_email, contact_phone, contact_links_json, assigned_staff_id, created_by_staff_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"{self.marker}-no-contact", f"https://youtube.com/@{self.marker}-no-contact", "youtube", "", "", "[]", self.admin_staff_id, self.admin_staff_id, self.now, self.now),
        )
        no_contact_kol = int(conn.execute("SELECT id FROM kols WHERE channel_name=?", (f"{self.marker}-no-contact",)).fetchone()["id"])
        conn.execute(
            """
            INSERT INTO vkpi_projects (project_uid, project_name, kol_id, assigned_staff_id, created_by_staff_id, product_sku, product_name, platform, stage, stage_status, started_at, last_activity_at, metadata_json, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (f"{self.marker}-no-contact-project", f"{self.marker} no contact project", no_contact_kol, self.admin_staff_id, self.admin_staff_id, f"{self.marker}-sku", "Smoke Lens", "youtube", "contacted", "active", self.now, self.now, _json({"marker": self.marker}), self.now, self.now),
        )
        conn.execute(
            "INSERT INTO vkpi_metric_runs (run_uid, period_start, period_end, scope_type, scope_id, trigger_source, generated_by_staff_id, generated_at, definition_version, status, metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"{self.marker}-metric-run", self.now, self.now, "all", None, "dashboard", self.admin_staff_id, self.now, "v1", "ready", _json({"marker": self.marker})),
        )
        metric_run_id = int(conn.execute("SELECT id FROM vkpi_metric_runs WHERE run_uid=?", (f"{self.marker}-metric-run",)).fetchone()["id"])
        conn.execute(
            "INSERT INTO vkpi_metric_values (run_id, metric_key, value_numeric, value_text, currency, unit, calculation_json, source_count, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (metric_run_id, "gmv", 12345, "", "USD", "cents", _json({"marker": self.marker, "purpose": "no source smoke"}), 0, self.now),
        )
        conn.execute(
            "INSERT INTO vkpi_kpi_ledger (ledger_date, staff_id, kol_id, project_id, metric_key, metric_value, source_type, source_ref, confidence, metadata_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (self.now[:10], self.admin_staff_id, kol_id, active_project, "workload_score", 42, "derived_kpi", f"kpi:{self.marker}:missing-breakdown", "confirmed", _json({"marker": self.marker, "purpose": "missing scoring breakdown smoke"}), self.now),
        )
        conn.execute(
            "INSERT INTO vkpi_report_runs (report_uid, report_type, period_start, period_end, scope_type, scope_id, metric_run_id, triggered_by_staff_id, triggered_at, status, summary_text, metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"{self.marker}-weekly-no-source-appendix", "weekly", self.now, self.now, "all", None, metric_run_id, self.admin_staff_id, self.now, "ready", f"{self.marker} weekly report missing source appendix", _json({"marker": self.marker})),
        )
        conn.commit()
        return {"active_project": active_project, "published_project": published_project, "shipped_project": shipped_project, "deleted_project": deleted_project, "kol_id": kol_id, "link_id": link_id, "snapshot_id": snapshot_id}

    def run(self) -> dict[str, Any]:
        self.cleanup()
        seed_ids = self.seed()
        try:
            first = self.request("GET", "/api/admin/vkpi/data-quality?limit=500")
            marker_issues = [issue for issue in first.get("issues", []) if self.marker in _json(issue)]
            issue_types = sorted({str(issue.get("issue_type")) for issue in marker_issues})
            required = {
                "pending_reconciliation",
                "unmatched_attribution",
                "missing_shopify_snapshot",
                "duplicate_shopify_order_credit",
                "refund_not_reflected",
                "amazon_missing_asin",
                "amazon_missing_campaign",
                "stale_amazon_report",
                "broken_link",
                "missing_utm",
                "published_without_content",
                "shipped_without_cost",
                "manual_attribution_without_evidence",
                "deleted_project_sales",
                "deleted_project_cost",
                "duplicate_kol_candidate",
                "metric_without_sources",
                "kpi_ledger_without_evidence",
                "weekly_report_without_source_appendix",
                "kol_missing_contact",
            }
            missing = sorted(required - set(issue_types))
            assert not missing, {"missing": missing, "found": issue_types, "marker_issues": marker_issues}

            employee_forbidden = self.request("POST", f"/api/admin/vkpi/data-quality/{marker_issues[0]['id']}/resolve", {"reason": self.marker}, token=self.employee_token, expect_status=403)
            assert employee_forbidden.get("status_code") == 403, employee_forbidden

            issue_to_resolve = next(issue for issue in marker_issues if issue.get("issue_type") == "missing_utm")
            assigned = self.request("POST", f"/api/admin/vkpi/data-quality/{issue_to_resolve['id']}/assign", {"reason": f"assign {self.marker}", "metadata": {"marker": self.marker}})
            assert assigned.get("action") == "assign", assigned
            rerun = self.request("POST", f"/api/admin/vkpi/data-quality/{issue_to_resolve['id']}/rerun", {"reason": f"rerun {self.marker}", "metadata": {"marker": self.marker}})
            assert rerun.get("action") == "rerun", rerun
            evidence = self.request("POST", f"/api/admin/vkpi/data-quality/{issue_to_resolve['id']}/evidence", {"reason": f"evidence {self.marker}", "metadata": {"marker": self.marker, "evidence_url": f"https://example.com/{self.marker}"}})
            assert evidence.get("action") == "evidence", evidence
            after_non_closing = self.request("GET", "/api/admin/vkpi/data-quality?limit=500")
            after_non_closing_issues = [issue for issue in after_non_closing.get("issues", []) if self.marker in _json(issue)]
            assert issue_to_resolve["id"] in {issue.get("id") for issue in after_non_closing_issues}, "non-closing actions hid issue"

            resolved = self.request("POST", f"/api/admin/vkpi/data-quality/{issue_to_resolve['id']}/resolve", {"reason": f"resolved {self.marker}", "metadata": {"marker": self.marker}})
            assert resolved.get("status") == "ok", resolved
            second = self.request("GET", "/api/admin/vkpi/data-quality?limit=500")
            second_marker_issues = [issue for issue in second.get("issues", []) if self.marker in _json(issue)]
            assert issue_to_resolve["id"] not in {issue.get("id") for issue in second_marker_issues}, "resolved issue still visible"
            reopened = self.request("POST", f"/api/admin/vkpi/data-quality/{issue_to_resolve['id']}/reopen", {"reason": f"reopened {self.marker}", "metadata": {"marker": self.marker}})
            assert reopened.get("action") == "reopen", reopened
            third = self.request("GET", "/api/admin/vkpi/data-quality?limit=500")
            third_marker_issues = [issue for issue in third.get("issues", []) if self.marker in _json(issue)]
            assert issue_to_resolve["id"] in {issue.get("id") for issue in third_marker_issues}, "reopened issue not visible"
            audit_rows = self.conn.execute(
                "SELECT action_type FROM vkpi_business_audit_logs WHERE target_id=? OR detail LIKE ? OR metadata_json LIKE ?",
                (str(issue_to_resolve["id"]), f"%{self.marker}%", f"%{self.marker}%"),
            ).fetchall()
            audit_actions = {str(row["action_type"]) for row in audit_rows}
            expected_audit_actions = {
                "data_quality_assign",
                "data_quality_rerun",
                "data_quality_evidence",
                "data_quality_resolve",
                "data_quality_reopen",
            }
            assert expected_audit_actions.issubset(audit_actions), {
                "missing_audit_actions": sorted(expected_audit_actions - audit_actions),
                "audit_actions": sorted(audit_actions),
            }

            cleanup_counts = self.cleanup()
            assert all(value == 0 for value in cleanup_counts.values()), cleanup_counts
            return {
                "marker": self.marker,
                "seed": seed_ids,
                "found_issue_types": issue_types,
                "resolved_issue_id": issue_to_resolve["id"],
                "non_closing_actions": [assigned.get("action"), rerun.get("action"), evidence.get("action")],
                "reopened_action": reopened.get("action"),
                "employee_resolve_status": employee_forbidden.get("status_code"),
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
    ensure_data_quality_schema()
    result = Smoke().run()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
