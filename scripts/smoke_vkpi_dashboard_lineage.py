#!/usr/bin/env python3
"""Smoke test for V-KPI dashboard lineage-first drilldown.

Seeds one temporary employee-owned KOL/project/link/order/cost/content/alert
chain, verifies dashboard metrics expose metric_value_id + source rows, verifies
employee latest-metric drilldown is forced to staff scope, then deletes all
smoke rows and generated metric runs.
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

BASE = "http://127.0.0.1:8102"
PREFIX = "vkpi-dashboard-lineage-smoke-"
ADMIN_USER_ID = int(os.environ.get("VKPI_SMOKE_ADMIN_USER_ID", "1"))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class Smoke:
    def __init__(self) -> None:
        self.marker = f"{PREFIX}{int(time.time())}"
        self.now = _now()
        self.conn = get_conn()
        self.admin_token = make_token(ADMIN_USER_ID, "admin")
        self.employee_user_id = 0
        self.employee_token = ""
        self.generated_run_ids: set[int] = set()

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        token: str | None = None,
        expect_status: int = 200,
    ) -> dict[str, Any]:
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

    def _delete_metric_runs(self, run_ids: set[int]) -> None:
        ids = sorted({int(run_id) for run_id in run_ids if int(run_id or 0)})
        if not ids:
            return
        ph = ",".join("?" for _ in ids)
        self.conn.execute(f"DELETE FROM vkpi_metric_sources WHERE metric_value_id IN (SELECT id FROM vkpi_metric_values WHERE run_id IN ({ph}))", ids)
        self.conn.execute(f"DELETE FROM vkpi_metric_values WHERE run_id IN ({ph})", ids)
        self.conn.execute(f"DELETE FROM vkpi_metric_runs WHERE id IN ({ph})", ids)

    def cleanup(self, marker: str | None = None) -> dict[str, int]:
        marker = marker or self.marker
        conn = self.conn
        like = f"%{marker}%"

        source_rows = conn.execute(
            "SELECT metric_value_id FROM vkpi_metric_sources WHERE snapshot_json LIKE ? OR evidence_ref LIKE ?",
            (like, like),
        ).fetchall()
        value_ids = sorted({int(row["metric_value_id"]) for row in source_rows if row["metric_value_id"] is not None})
        run_ids = set(self.generated_run_ids)
        if value_ids:
            ph = ",".join("?" for _ in value_ids)
            run_ids.update(
                int(row["run_id"])
                for row in conn.execute(f"SELECT DISTINCT run_id FROM vkpi_metric_values WHERE id IN ({ph})", value_ids).fetchall()
            )
        self._delete_metric_runs(run_ids)

        project_ids = [
            int(row["id"])
            for row in conn.execute(
                "SELECT id FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?",
                (like, like, like),
            ).fetchall()
        ]
        kol_ids = [
            int(row["id"])
            for row in conn.execute(
                "SELECT id FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?",
                (like, like, like),
            ).fetchall()
        ]
        link_ids = [
            int(row["id"])
            for row in conn.execute(
                "SELECT id FROM vkpi_links WHERE slug LIKE ? OR link_uid LIKE ? OR campaign_name LIKE ? OR metadata_json LIKE ?",
                (like, like, like, like),
            ).fetchall()
        ]
        post_ids = [
            int(row["id"])
            for row in conn.execute(
                "SELECT id FROM vkpi_content_posts WHERE post_url LIKE ? OR title LIKE ? OR metadata_json LIKE ?",
                (like, like, like),
            ).fetchall()
        ]
        attr_ids = [
            int(row["id"])
            for row in conn.execute(
                """
                SELECT id FROM vkpi_sales_attributions
                WHERE source_ref LIKE ? OR evidence_json LIKE ? OR product_sku LIKE ?
                   OR shopify_order_snapshot_id IN (
                       SELECT id FROM vkpi_shopify_order_snapshots
                       WHERE shopify_order_id LIKE ? OR raw_payload_json LIKE ? OR order_name LIKE ?
                   )
                """,
                (like, like, like, like, like, like),
            ).fetchall()
        ]
        user_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchall()]

        def delete_in(table: str, column: str, ids: list[int]) -> None:
            if not ids:
                return
            ph = ",".join("?" for _ in ids)
            conn.execute(f"DELETE FROM {table} WHERE {column} IN ({ph})", ids)

        delete_in("vkpi_attribution_adjustments", "attribution_id", attr_ids)
        delete_in("vkpi_sales_attributions", "id", attr_ids)
        delete_in("vkpi_link_clicks", "link_id", link_ids)
        conn.execute("DELETE FROM vkpi_link_clicks WHERE event_id LIKE ? OR metadata_json LIKE ?", (like, like))
        delete_in("vkpi_links", "id", link_ids)
        delete_in("vkpi_content_assets", "post_id", post_ids)
        delete_in("vkpi_content_assets", "project_id", project_ids)
        delete_in("vkpi_content_posts", "id", post_ids)
        delete_in("vkpi_content_posts", "project_id", project_ids)
        delete_in("vkpi_cost_ledger", "project_id", project_ids)
        conn.execute("DELETE FROM vkpi_cost_ledger WHERE source_ref LIKE ? OR note LIKE ? OR metadata_json LIKE ?", (like, like, like))
        delete_in("vkpi_project_stage_events", "project_id", project_ids)
        delete_in("vkpi_alerts", "target_id", project_ids)
        conn.execute("DELETE FROM vkpi_alerts WHERE alert_key LIKE ? OR title LIKE ? OR metadata_json LIKE ?", (like, like, like))
        delete_in("vkpi_projects", "id", project_ids)
        delete_in("vkpi_kol_claims", "kol_id", kol_ids)
        delete_in("kols", "id", kol_ids)
        conn.execute(
            "DELETE FROM vkpi_shopify_order_snapshots WHERE shopify_order_id LIKE ? OR raw_payload_json LIKE ? OR order_name LIKE ? OR landing_site LIKE ?",
            (like, like, like, like),
        )
        delete_in("staff", "user_id", user_ids)
        delete_in("users", "id", user_ids)
        conn.commit()

        return {
            "metric_sources": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_metric_sources WHERE snapshot_json LIKE ? OR evidence_ref LIKE ?", (like, like)).fetchone()["n"]),
            "metric_runs": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_metric_runs WHERE id IN (%s)" % ",".join(str(i) for i in run_ids) if run_ids else "SELECT 0 AS n").fetchone()["n"]) if run_ids else 0,
            "projects": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "kols": int(conn.execute("SELECT COUNT(*) AS n FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?", (like, like, like)).fetchone()["n"]),
            "links": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_links WHERE slug LIKE ? OR link_uid LIKE ? OR campaign_name LIKE ? OR metadata_json LIKE ?", (like, like, like, like)).fetchone()["n"]),
            "clicks": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_link_clicks WHERE event_id LIKE ? OR metadata_json LIKE ?", (like, like)).fetchone()["n"]),
            "content_posts": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_content_posts WHERE post_url LIKE ? OR title LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "costs": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_cost_ledger WHERE source_ref LIKE ? OR note LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "attributions": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_sales_attributions WHERE source_ref LIKE ? OR evidence_json LIKE ? OR product_sku LIKE ?", (like, like, like)).fetchone()["n"]),
            "snapshots": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_shopify_order_snapshots WHERE shopify_order_id LIKE ? OR raw_payload_json LIKE ? OR order_name LIKE ? OR landing_site LIKE ?", (like, like, like, like)).fetchone()["n"]),
            "alerts": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_alerts WHERE alert_key LIKE ? OR title LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "users": int(conn.execute("SELECT COUNT(*) AS n FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchone()["n"]),
        }

    def seed(self) -> dict[str, int]:
        conn = self.conn
        conn.execute(
            "INSERT INTO users (created_at, email, password_hash, name, status, role, email_verified) VALUES (?,?,?,?,?,?,?)",
            (self.now, f"{self.marker}@example.com", "v2:00:00", self.marker, "approved", "employee", 1),
        )
        self.employee_user_id = int(conn.execute("SELECT id FROM users WHERE email=?", (f"{self.marker}@example.com",)).fetchone()["id"])
        self.employee_token = make_token(self.employee_user_id, "employee")
        staff_cols = {str(row["name"]) for row in conn.execute("PRAGMA table_info(staff)").fetchall()}
        insert_cols = ["user_id", "role", "permissions_json", "mfa_enabled", "active", "invited_by", "invited_at"]
        values: list[Any] = [self.employee_user_id, "employee", _json({"vkpi": "write"}), 0, 1, None, self.now]
        if "is_owner" in staff_cols:
            insert_cols.append("is_owner")
            values.append(0)
        if "email_domain_verified" in staff_cols:
            insert_cols.append("email_domain_verified")
            values.append(1)
        placeholders = ",".join("?" for _ in insert_cols)
        conn.execute(f"INSERT INTO staff ({', '.join(insert_cols)}) VALUES ({placeholders})", values)
        staff_id = int(conn.execute("SELECT id FROM staff WHERE user_id=?", (self.employee_user_id,)).fetchone()["id"])
        sku = f"{self.marker}-sku"

        conn.execute(
            "INSERT INTO kols (channel_name, channel_url, platform, contact_email, assigned_staff_id, created_by_staff_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (self.marker, f"https://instagram.com/{self.marker}", "instagram", f"{self.marker}@creator.test", staff_id, staff_id, self.now, self.now),
        )
        kol_id = int(conn.execute("SELECT id FROM kols WHERE channel_name=?", (self.marker,)).fetchone()["id"])
        conn.execute(
            "INSERT INTO vkpi_kol_claims (kol_id, staff_id, project_id, status, claimed_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (kol_id, staff_id, None, "active", self.now, self.now, self.now),
        )
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
                f"{self.marker} dashboard lineage",
                kol_id,
                staff_id,
                staff_id,
                sku,
                "Dashboard Smoke Lens",
                "instagram",
                "agreed",
                "active",
                self.now,
                self.now,
                _json({"marker": self.marker}),
                self.now,
                self.now,
            ),
        )
        project_id = int(conn.execute("SELECT id FROM vkpi_projects WHERE project_uid=?", (self.marker,)).fetchone()["id"])
        conn.execute("UPDATE vkpi_kol_claims SET project_id=? WHERE kol_id=?", (project_id, kol_id))
        conn.execute(
            "INSERT INTO vkpi_project_stage_events (project_id, from_stage, to_stage, event_type, actor_staff_id, note, source_ref_type, source_ref_id, effective_at, metadata_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, "received", "published", "publish", staff_id, self.marker, "content", f"https://instagram.com/p/{self.marker}", self.now, _json({"marker": self.marker}), self.now),
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
                f"{self.marker}-slug",
                "shopify",
                f"https://viltrox.com/products/{self.marker}",
                "shopify",
                sku,
                self.marker,
                kol_id,
                project_id,
                staff_id,
                staff_id,
                "active",
                302,
                "allowed",
                "standard",
                "instagram",
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
        link_id = int(conn.execute("SELECT id FROM vkpi_links WHERE link_uid=?", (self.marker,)).fetchone()["id"])
        conn.execute(
            "INSERT INTO vkpi_link_clicks (link_id, event_id, clicked_at, ip_hash, user_agent, referrer, country_code, device_type, bot_score, is_bot, is_unique, session_id, destination_url, metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (link_id, f"{self.marker}-click", self.now, "smoke", "SmokeBrowser/1.0", "https://instagram.com", "US", "desktop", 0, 0, 1, self.marker, f"https://viltrox.com/products/{self.marker}", _json({"marker": self.marker})),
        )
        conn.execute(
            "INSERT INTO vkpi_content_posts (project_id, kol_id, link_id, platform, post_url, title, thumbnail_url, published_at, content_type, views, likes, comments, shares, rights_status, ad_usage_allowed, metadata_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, kol_id, link_id, "instagram", f"https://instagram.com/p/{self.marker}", self.marker, f"https://example.com/{self.marker}.jpg", self.now, "reel", 4321, 210, 18, 6, "owned", True, _json({"marker": self.marker}), self.now, self.now),
        )
        conn.execute(
            "INSERT INTO vkpi_cost_ledger (project_id, kol_id, staff_id, cost_type, amount_cents, currency, status, incurred_at, source_ref, note, created_by_staff_id, metadata_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, kol_id, staff_id, "shipping", 39900, "USD", "actual", self.now, f"cost:{self.marker}", self.marker, staff_id, _json({"marker": self.marker}), self.now),
        )
        shopify_order_id = f"gid://shopify/Order/{int(time.time() * 1000)}"
        conn.execute(
            "INSERT INTO vkpi_shopify_order_snapshots (shopify_order_id, admin_graphql_api_id, order_name, order_number, processed_at, currency, subtotal_cents, total_cents, financial_status, fulfillment_status, refund_status, discount_codes_json, landing_site, note_attributes_json, line_items_json, raw_payload_hash, raw_payload_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (shopify_order_id, shopify_order_id, f"#{self.marker}", self.marker[-6:], self.now, "USD", 129900, 129900, "paid", "fulfilled", "", "[]", f"https://viltrox.com/products/{self.marker}", _json({"vkpi_click_id": f"{self.marker}-click"}), _json([{"sku": sku}]), self.marker, _json({"marker": self.marker}), self.now, self.now),
        )
        snapshot_id = int(conn.execute("SELECT id FROM vkpi_shopify_order_snapshots WHERE shopify_order_id=?", (shopify_order_id,)).fetchone()["id"])
        conn.execute(
            "INSERT INTO vkpi_sales_attributions (source_platform, source_ref, project_id, link_id, kol_id, staff_id, shopify_order_snapshot_id, product_sku, revenue_cents, currency, confidence, occurred_at, imported_at, evidence_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("shopify", f"shopify:{self.marker}", project_id, link_id, kol_id, staff_id, snapshot_id, sku, 129900, "USD", "confirmed", self.now, self.now, _json({"marker": self.marker}), self.now),
        )
        conn.execute(
            "INSERT INTO vkpi_alerts (alert_key, severity, status, target_type, target_id, staff_id, title, body, rule_key, due_at, metadata_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.marker, "warning", "open", "project", project_id, staff_id, self.marker, "dashboard lineage smoke", "smoke", self.now, _json({"marker": self.marker}), self.now, self.now),
        )
        conn.commit()
        return {"staff_id": staff_id, "kol_id": kol_id, "project_id": project_id, "link_id": link_id, "snapshot_id": snapshot_id}

    def metric_map(self, payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
        run_id = _int((payload.get("metric_run") or {}).get("id"))
        if run_id:
            self.generated_run_ids.add(run_id)
        return {str(item.get("metric_key")): item for item in payload.get("metrics", [])}

    def assert_metric_has_marker_source(self, metric: dict[str, Any], metric_key: str) -> None:
        metric_value_id = _int(metric.get("metric_value_id") or metric.get("metricValueId"))
        assert metric_value_id > 0, {"metric_key": metric_key, "metric": metric}
        assert _int(metric.get("source_count")) > 0, {"metric_key": metric_key, "metric": metric}
        drill = self.request("GET", f"/api/admin/vkpi/lineage/values/{metric_value_id}/drilldown?limit=500")
        rows = drill.get("rows") or []
        assert rows, {"metric_key": metric_key, "drilldown": drill}
        matched = [row for row in rows if self.marker in _json(row)]
        assert matched, {"metric_key": metric_key, "row_count": len(rows), "sample": rows[:3]}

    def run(self) -> dict[str, Any]:
        self.cleanup()
        seed = self.seed()
        try:
            dashboard = self.request("GET", "/api/admin/vkpi/dashboard?window_days=7")
            metrics = self.metric_map(dashboard)
            required_marker_metrics = [
                "gmv",
                "cost",
                "new_kol",
                "published_content",
                "valid_clicks",
                "views",
                "active_projects",
                "alerts",
            ]
            missing = [key for key in required_marker_metrics if key not in metrics]
            assert not missing, {"missing_metrics": missing, "metrics": sorted(metrics)}
            for key in required_marker_metrics:
                self.assert_metric_has_marker_source(metrics[key], key)

            for derived_key in ("net_contribution", "roi"):
                metric = metrics.get(derived_key)
                assert metric and _int(metric.get("metric_value_id") or metric.get("metricValueId")) > 0, metric
                assert _int(metric.get("source_count")) >= 2, metric
                drill = self.request("GET", f"/api/admin/vkpi/lineage/values/{_int(metric.get('metric_value_id') or metric.get('metricValueId'))}/drilldown?limit=20")
                source_metrics = sorted({str(((row.get("snapshot") or {}).get("source_metric") or "")) for row in drill.get("rows", [])})
                assert {"cost", "gmv"}.issubset(set(source_metrics)), {"derived_key": derived_key, "source_metrics": source_metrics, "rows": drill.get("rows")}

            employee_dashboard = self.request("GET", "/api/admin/vkpi/dashboard/view/employee?window_days=7", token=self.employee_token)
            employee_metrics = self.metric_map(employee_dashboard)
            employee_gmv = employee_metrics.get("gmv") or {}
            employee_gmv_id = _int(employee_gmv.get("metric_value_id") or employee_gmv.get("metricValueId"))
            assert employee_gmv_id > 0, employee_gmv
            employee_drill = self.request("GET", f"/api/admin/vkpi/lineage/values/{employee_gmv_id}/drilldown?limit=100", token=self.employee_token)
            assert any(self.marker in _json(row) for row in employee_drill.get("rows", [])), employee_drill

            latest_drill = self.request("GET", "/api/admin/vkpi/lineage/metrics/gmv/drilldown?scope_type=all&limit=100", token=self.employee_token)
            assert latest_drill.get("row_count", 0) >= 1, latest_drill
            assert any(self.marker in _json(row) for row in latest_drill.get("rows", [])), latest_drill

            cleanup_counts = self.cleanup()
            assert all(value == 0 for value in cleanup_counts.values()), cleanup_counts
            return {
                "marker": self.marker,
                "seed": seed,
                "admin_metric_keys": sorted(metrics),
                "employee_gmv_metric_value_id": employee_gmv_id,
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
    result = Smoke().run()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
