#!/usr/bin/env python3
"""Smoke test for V-KPI reports/export source appendix and download audit.

Seeds a tiny, marker-scoped business chain, generates a weekly PDF report,
verifies metric source rows are attached to the report context and the PDF is
servable, verifies report/export downloads write audit rows, then removes all
smoke rows and files.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("DB_RUNTIME_BACKEND", "sqlite")
os.environ.setdefault("DATABASE_URL", "")

from app.core.security import make_token
from app.db.connection import get_conn
from app.services.vkpi.schema import ensure_vkpi_schema
from app.services.vkpi.schema_audit import ensure_vkpi_audit_schema
from app.services.vkpi.schema_lineage import ensure_vkpi_lineage_schema
from app.domains.reports import ensure_vkpi_reports_schema

BASE = os.environ.get("VKPI_SMOKE_BASE", "http://127.0.0.1:8102")
PREFIX = "vkpi-report-export-smoke-"
STAFF_ID = 3  # fallback only; smoke creates an isolated admin staff row at runtime


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Smoke:
    def __init__(self) -> None:
        self.marker = f"{PREFIX}{int(time.time())}"
        self.now = _now()
        self.day = self.now[:10]
        self.conn = get_conn()
        self.user_id = 0
        self.staff_id = 0
        self.token = ""
        self.report_run_id = 0
        self.export_id = 0

    def request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if payload is None else _json(payload).encode("utf-8")
        req = urllib.request.Request(BASE + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"HTTP {exc.code} {method} {path}: {body[:800]}") from exc

    def request_bytes(self, path: str) -> bytes:
        req = urllib.request.Request(BASE + path, method="GET")
        req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"HTTP {exc.code} GET {path}: {body[:800]}") from exc

    def seed_actor(self) -> None:
        c = self.conn
        email = f"{self.marker}@viltrox.com"
        c.execute(
            "INSERT INTO users (created_at, email, password_hash, name, status, role, email_verified, avatar_url) VALUES (?,?,?,?,?,?,?,?)",
            (self.now, email, "v2:00:00", self.marker, "approved", "admin", 1, f"https://avatar.example/{self.marker}.png"),
        )
        self.user_id = int(c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"])
        staff_cols = {str(row["name"]) for row in c.execute("PRAGMA table_info(staff)").fetchall()}
        insert_cols = ["user_id", "role", "permissions_json", "mfa_enabled", "active", "invited_by", "invited_at"]
        values: list[Any] = [
            self.user_id,
            "admin",
            _json({"vkpi": "write"}),
            0,
            1,
            None,
            self.now,
        ]
        if "is_owner" in staff_cols:
            insert_cols.append("is_owner")
            values.append(1)
        if "email_domain_verified" in staff_cols:
            insert_cols.append("email_domain_verified")
            values.append(1)
        placeholders = ",".join("?" for _ in insert_cols)
        c.execute(f"INSERT INTO staff ({', '.join(insert_cols)}) VALUES ({placeholders})", values)
        self.staff_id = int(c.execute("SELECT id FROM staff WHERE user_id=?", (self.user_id,)).fetchone()["id"])
        self.token = make_token(self.user_id, "admin")
        c.commit()

    def seed(self) -> dict[str, int]:
        c = self.conn
        self.seed_actor()
        sku = f"{self.marker}-sku"
        c.execute(
            "INSERT INTO kols (channel_name, channel_url, platform, contact_email, assigned_staff_id, created_by_staff_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (self.marker, f"https://instagram.com/{self.marker}", "instagram", f"{self.marker}@creator.test", self.staff_id, self.staff_id, self.now, self.now),
        )
        kol_id = int(c.execute("SELECT id FROM kols WHERE channel_name=?", (self.marker,)).fetchone()["id"])
        c.execute(
            """
            INSERT INTO vkpi_projects (
                project_uid, project_name, kol_id, assigned_staff_id, created_by_staff_id,
                product_sku, product_name, platform, stage, stage_status, started_at,
                last_activity_at, metadata_json, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self.marker,
                f"{self.marker} 周报证据项目",
                kol_id,
                self.staff_id,
                self.staff_id,
                sku,
                "Smoke 35mm F1.2",
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
        project_id = int(c.execute("SELECT id FROM vkpi_projects WHERE project_uid=?", (self.marker,)).fetchone()["id"])
        c.execute(
            "INSERT INTO vkpi_kol_claims (kol_id, staff_id, project_id, status, claimed_at, last_effective_touch_at, metadata_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (kol_id, self.staff_id, project_id, "active", self.now, self.now, _json({"marker": self.marker}), self.now, self.now),
        )
        c.execute(
            "INSERT INTO vkpi_project_stage_events (project_id, from_stage, to_stage, event_type, actor_staff_id, note, source_ref_type, source_ref_id, effective_at, metadata_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, "received", "published", "publish", self.staff_id, self.marker, "content_post", f"{self.marker}-post", self.now, _json({"marker": self.marker}), self.now),
        )
        c.execute(
            "INSERT INTO vkpi_content_posts (project_id, kol_id, platform, post_url, title, published_at, content_type, views, likes, comments, shares, metadata_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, kol_id, "instagram", f"https://instagram.com/p/{self.marker}", self.marker, self.now, "reel", 4321, 321, 45, 12, _json({"marker": self.marker}), self.now, self.now),
        )
        c.execute(
            "INSERT INTO vkpi_cost_ledger (project_id, kol_id, staff_id, cost_type, amount_cents, currency, status, incurred_at, source_ref, note, created_by_staff_id, metadata_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, kol_id, self.staff_id, "shipping", 12345, "USD", "actual", self.now, f"{self.marker}-shipping", self.marker, self.staff_id, _json({"marker": self.marker}), self.now),
        )
        c.execute(
            "INSERT INTO vkpi_sales_attributions (source_platform, source_ref, project_id, kol_id, staff_id, product_sku, revenue_cents, currency, attribution_model, confidence, occurred_at, imported_at, evidence_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("shopify", f"{self.marker}-order", project_id, kol_id, self.staff_id, sku, 67890, "USD", "last_touch", "confirmed", self.now, self.now, _json({"marker": self.marker, "source_ref": f"{self.marker}-order"}), self.now),
        )
        c.commit()
        return {"kol_id": kol_id, "project_id": project_id}

    def cleanup(self) -> dict[str, int]:
        c = self.conn
        like = f"%{self.marker}%"
        report_ids = [int(r["id"]) for r in c.execute("SELECT id FROM vkpi_report_runs WHERE metadata_json LIKE ? OR summary_text LIKE ?", (like, like)).fetchall()]
        export_ids = [int(r["id"]) for r in c.execute("SELECT id FROM vkpi_export_jobs WHERE filters_json LIKE ? OR export_uid LIKE ?", (like, like)).fetchall()]
        run_ids = [int(r["metric_run_id"]) for r in c.execute("SELECT metric_run_id FROM vkpi_report_runs WHERE metadata_json LIKE ? AND metric_run_id IS NOT NULL", (like,)).fetchall()]
        file_paths = [str(r["file_path"] or "") for r in c.execute("SELECT file_path FROM vkpi_report_files WHERE report_run_id IN (%s)" % ",".join("?" for _ in report_ids), report_ids).fetchall()] if report_ids else []
        file_paths += [str(r["file_path"] or "") for r in c.execute("SELECT file_path FROM vkpi_export_jobs WHERE id IN (%s)" % ",".join("?" for _ in export_ids), export_ids).fetchall()] if export_ids else []
        for path in file_paths:
            try:
                if path and Path(path).exists():
                    Path(path).unlink()
            except Exception:
                pass

        if run_ids:
            ph = ",".join("?" for _ in run_ids)
            c.execute(f"DELETE FROM vkpi_metric_sources WHERE metric_value_id IN (SELECT id FROM vkpi_metric_values WHERE run_id IN ({ph}))", run_ids)
            c.execute(f"DELETE FROM vkpi_metric_values WHERE run_id IN ({ph})", run_ids)
            c.execute(f"DELETE FROM vkpi_metric_runs WHERE id IN ({ph})", run_ids)
        if report_ids:
            ph = ",".join("?" for _ in report_ids)
            c.execute(f"DELETE FROM vkpi_report_files WHERE report_run_id IN ({ph})", report_ids)
            c.execute(f"DELETE FROM vkpi_report_runs WHERE id IN ({ph})", report_ids)
            c.execute(f"DELETE FROM vkpi_sensitive_access_logs WHERE resource_type='report' AND resource_id IN ({ph})", [str(i) for i in report_ids])
            c.execute(f"DELETE FROM vkpi_business_audit_logs WHERE target_type='report' AND target_id IN ({ph})", [str(i) for i in report_ids])
        if export_ids:
            ph = ",".join("?" for _ in export_ids)
            c.execute(f"DELETE FROM vkpi_export_jobs WHERE id IN ({ph})", export_ids)
            c.execute(f"DELETE FROM vkpi_sensitive_access_logs WHERE resource_type='export' AND resource_id IN ({ph})", [str(i) for i in export_ids])
            c.execute(f"DELETE FROM vkpi_business_audit_logs WHERE target_type='export' AND target_id IN ({ph})", [str(i) for i in export_ids])
        c.execute("DELETE FROM vkpi_export_logs WHERE filters_json LIKE ? OR download_url LIKE ?", (like, like))

        project_ids = [int(r["id"]) for r in c.execute("SELECT id FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchall()]
        kol_ids = [int(r["id"]) for r in c.execute("SELECT id FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?", (like, like, like)).fetchall()]
        attr_ids = [int(r["id"]) for r in c.execute("SELECT id FROM vkpi_sales_attributions WHERE source_ref LIKE ? OR evidence_json LIKE ? OR product_sku LIKE ?", (like, like, like)).fetchall()]
        post_ids = [int(r["id"]) for r in c.execute("SELECT id FROM vkpi_content_posts WHERE post_url LIKE ? OR title LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchall()]
        user_ids = [int(r["id"]) for r in c.execute("SELECT id FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchall()]
        staff_ids = [int(r["id"]) for r in c.execute("SELECT id FROM staff WHERE user_id IN (%s)" % ",".join("?" for _ in user_ids), user_ids).fetchall()] if user_ids else []

        def delete_in(table: str, column: str, ids: list[int]) -> None:
            if not ids:
                return
            ph = ",".join("?" for _ in ids)
            c.execute(f"DELETE FROM {table} WHERE {column} IN ({ph})", ids)

        delete_in("vkpi_attribution_adjustments", "attribution_id", attr_ids)
        delete_in("vkpi_sales_attributions", "id", attr_ids)
        delete_in("vkpi_content_assets", "post_id", post_ids)
        delete_in("vkpi_content_posts", "id", post_ids)
        c.execute("DELETE FROM vkpi_kpi_ledger WHERE source_ref LIKE ? OR metadata_json LIKE ?", (like, like))
        delete_in("vkpi_cost_ledger", "project_id", project_ids)
        c.execute("DELETE FROM vkpi_cost_ledger WHERE source_ref LIKE ? OR note LIKE ? OR metadata_json LIKE ?", (like, like, like))
        delete_in("vkpi_project_stage_events", "project_id", project_ids)
        delete_in("vkpi_kol_claims", "kol_id", kol_ids)
        delete_in("vkpi_projects", "id", project_ids)
        delete_in("kols", "id", kol_ids)
        delete_in("vkpi_business_audit_logs", "staff_id", staff_ids)
        delete_in("vkpi_sensitive_access_logs", "staff_id", staff_ids)
        delete_in("staff", "user_id", user_ids)
        delete_in("users", "id", user_ids)
        c.commit()

        return {
            "reports": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_report_runs WHERE metadata_json LIKE ? OR summary_text LIKE ?", (like, like)).fetchone()["n"]),
            "exports": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_export_jobs WHERE filters_json LIKE ? OR export_uid LIKE ?", (like, like)).fetchone()["n"]),
            "metric_sources": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_metric_sources WHERE snapshot_json LIKE ? OR evidence_ref LIKE ?", (like, like)).fetchone()["n"]),
            "kpi_ledger": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_kpi_ledger WHERE source_ref LIKE ? OR metadata_json LIKE ?", (like, like)).fetchone()["n"]),
            "business_audit": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE metadata_json LIKE ? OR detail LIKE ?", (like, like)).fetchone()["n"]),
            "projects": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "kols": int(c.execute("SELECT COUNT(*) AS n FROM kols WHERE channel_name LIKE ? OR channel_url LIKE ? OR contact_email LIKE ?", (like, like, like)).fetchone()["n"]),
            "costs": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_cost_ledger WHERE source_ref LIKE ? OR note LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "attributions": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_sales_attributions WHERE source_ref LIKE ? OR evidence_json LIKE ? OR product_sku LIKE ?", (like, like, like)).fetchone()["n"]),
            "content_posts": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_content_posts WHERE post_url LIKE ? OR title LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
            "users": int(c.execute("SELECT COUNT(*) AS n FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchone()["n"]),
        }

    def run(self) -> dict[str, Any]:
        ensure_vkpi_schema()
        ensure_vkpi_lineage_schema()
        ensure_vkpi_reports_schema()
        ensure_vkpi_audit_schema()
        self.cleanup()
        seeded = self.seed()
        rollup = self.request_json("POST", "/api/admin/vkpi/rollups/run-now", {"ledger_date": self.day, "staff_id": self.staff_id})
        if int(rollup.get("inserted") or 0) < 1:
            raise AssertionError(f"KPI rollup did not insert source rows: {rollup}")
        payload = {"period_days": 7, "staff_id": self.staff_id, "marker": self.marker}
        # Use the same public alias that the topbar buttons call from the UI.
        report = self.request_json("POST", "/api/marketing/reports/weekly/generate", payload)
        self.report_run_id = int(report.get("report_run_id") or report.get("reportRunId") or 0)
        if not self.report_run_id:
            raise AssertionError(f"missing report_run_id: {report}")
        report_row = self.conn.execute("SELECT * FROM vkpi_report_runs WHERE id=?", (self.report_run_id,)).fetchone()
        if not report_row or not int(report_row["metric_run_id"] or 0):
            raise AssertionError("weekly report did not persist metric_run_id")
        metric_run_id = int(report_row["metric_run_id"])
        source_count = int(self.conn.execute("SELECT COUNT(*) AS n FROM vkpi_metric_sources WHERE metric_value_id IN (SELECT id FROM vkpi_metric_values WHERE run_id=?)", (metric_run_id,)).fetchone()["n"])
        if source_count < 4:
            raise AssertionError(f"expected metric source rows, got {source_count}")
        if int(report.get("kpi_appendix_source_count") or 0) < 2:
            raise AssertionError(f"weekly report response missing KPI appendix source rows: {report}")
        if int(report.get("kpi_appendix_formula_count") or 0) < 2:
            raise AssertionError(f"weekly report response missing KPI formula rows: {report}")
        kpi_formula_rows = self.conn.execute(
            "SELECT metric_key, metadata_json FROM vkpi_kpi_ledger WHERE staff_id=? AND metric_key IN ('workload_score', 'kpi_credit') ORDER BY id DESC",
            (self.staff_id,),
        ).fetchall()
        formulas = [json.loads(row["metadata_json"]).get("formula") for row in kpi_formula_rows]
        if "sum(metric_value * workload_weight)" not in formulas:
            raise AssertionError(f"KPI ledger missing workload formula: {formulas}")
        if "workload_score + max(net_contribution_cents, 0) / 10000" not in formulas:
            raise AssertionError(f"KPI ledger missing KPI credit formula: {formulas}")
        report_download_url = str(report.get("download_url") or report.get("downloadUrl") or "")
        if not report_download_url:
            raise AssertionError(f"weekly report response missing download url: {report}")
        pdf = self.request_bytes(report_download_url)
        if not pdf.startswith(b"%PDF") or len(pdf) < 5000:
            raise AssertionError("weekly PDF download is not a valid PDF")
        file_row = self.conn.execute("SELECT download_count, last_downloaded_by_staff_id FROM vkpi_report_files WHERE report_run_id=? ORDER BY id DESC LIMIT 1", (self.report_run_id,)).fetchone()
        if not file_row or int(file_row["download_count"] or 0) < 1 or int(file_row["last_downloaded_by_staff_id"] or 0) != self.staff_id:
            raise AssertionError("report download counter not updated")
        report_audit = int(self.conn.execute("SELECT COUNT(*) AS n FROM vkpi_sensitive_access_logs WHERE action_type='download_report' AND resource_type='report' AND resource_id=?", (str(self.report_run_id),)).fetchone()["n"])
        if report_audit < 1:
            raise AssertionError("download_report sensitive audit missing")
        report_business = int(self.conn.execute("SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE action_type='report_download' AND target_type='report' AND target_id=?", (str(self.report_run_id),)).fetchone()["n"])
        if report_business < 1:
            raise AssertionError("report_download business audit missing")

        export = self.request_json("POST", "/api/marketing/exports/csv", {"report_type": "projects", "staff_id": self.staff_id, "marker": self.marker})
        self.export_id = int(export.get("export_id") or export.get("exportId") or 0)
        if not self.export_id:
            raise AssertionError(f"missing export id: {export}")
        export_download_url = str(export.get("download_url") or export.get("downloadUrl") or "")
        if not export_download_url:
            raise AssertionError(f"csv export response missing download url: {export}")
        csv_bytes = self.request_bytes(export_download_url)
        if b"project_name" not in csv_bytes and b"project_uid" not in csv_bytes:
            raise AssertionError("csv export did not contain project fields")
        export_audit = int(self.conn.execute("SELECT COUNT(*) AS n FROM vkpi_sensitive_access_logs WHERE action_type='download_export' AND resource_type='export' AND resource_id=?", (str(self.export_id),)).fetchone()["n"])
        if export_audit < 1:
            raise AssertionError("download_export sensitive audit missing")
        export_business = int(self.conn.execute("SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE action_type='export_download' AND target_type='export' AND target_id=?", (str(self.export_id),)).fetchone()["n"])
        if export_business < 1:
            raise AssertionError("export_download business audit missing")
        residue = self.cleanup()
        if any(residue.values()):
            raise AssertionError(f"smoke residue not cleaned: {residue}")
        return {"ok": True, "marker": self.marker, "seeded": seeded, "metric_source_count": source_count, "residue": residue}


if __name__ == "__main__":
    smoke = Smoke()
    try:
        print(json.dumps(smoke.run(), ensure_ascii=False, indent=2))
    except Exception:
        residue = smoke.cleanup()
        print(json.dumps({"ok": False, "marker": smoke.marker, "cleanup_after_failure": residue}, ensure_ascii=False, indent=2))
        raise
