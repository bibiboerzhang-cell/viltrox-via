#!/usr/bin/env python3
"""Smoke test for V-KPI AI weekly summary.

Covers two paths:
- forced fallback path with VKPI_WEEKLY_SUMMARY_AI_DISABLED=1
- gateway fallback path with external providers forced offline

All seeded rows are marker-scoped and removed at the end.
"""
from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("DB_RUNTIME_BACKEND", "sqlite")
os.environ.setdefault("DATABASE_URL", "")

from app.db.connection import get_conn
from app.domains import reports
from app.services.vkpi.schema import ensure_vkpi_schema
from app.services.vkpi.schema_lineage import ensure_vkpi_lineage_schema
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema
from app.domains.reports import ensure_vkpi_reports_schema

PREFIX = "vkpi-weekly-ai-smoke-"


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Smoke:
    def __init__(self) -> None:
        self.marker = f"{PREFIX}{int(time.time())}"
        self.now = _now()
        self.conn = get_conn()
        self.user_id = 0
        self.staff_id = 0

    def seed_actor(self) -> None:
        c = self.conn
        email = f"{self.marker}@viltrox.test"
        c.execute(
            "INSERT INTO users (created_at, email, password_hash, name, status, role, email_verified, avatar_url) VALUES (?,?,?,?,?,?,?,?)",
            (self.now, email, "v2:00:00", self.marker, "approved", "admin", 1, f"https://avatar.example/{self.marker}.png"),
        )
        self.user_id = int(c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"])
        staff_cols = {str(row["name"]) for row in c.execute("PRAGMA table_info(staff)").fetchall()}
        insert_cols = ["user_id", "role", "permissions_json", "mfa_enabled", "active", "invited_by", "invited_at"]
        values: list[Any] = [self.user_id, "admin", _json({"vkpi": "write"}), 0, 1, None, self.now]
        if "is_owner" in staff_cols:
            insert_cols.append("is_owner")
            values.append(1)
        if "email_domain_verified" in staff_cols:
            insert_cols.append("email_domain_verified")
            values.append(1)
        placeholders = ",".join("?" for _ in insert_cols)
        c.execute(f"INSERT INTO staff ({', '.join(insert_cols)}) VALUES ({placeholders})", values)
        self.staff_id = int(c.execute("SELECT id FROM staff WHERE user_id=?", (self.user_id,)).fetchone()["id"])
        c.commit()

    def seed_business_rows(self) -> dict[str, int]:
        c = self.conn
        self.seed_actor()
        sku = f"{self.marker}-sku"
        c.execute(
            "INSERT INTO kols (channel_name, channel_url, platform, contact_email, assigned_staff_id, created_by_staff_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (self.marker, f"https://youtube.com/@{self.marker}", "youtube", f"{self.marker}@creator.test", self.staff_id, self.staff_id, self.now, self.now),
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
                f"{self.marker} AI 周报项目",
                kol_id,
                self.staff_id,
                self.staff_id,
                sku,
                "Smoke AI 35mm F1.2",
                "youtube",
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
            (project_id, kol_id, "youtube", f"https://youtube.com/watch?v={self.marker}", self.marker, self.now, "review", 9876, 765, 88, 21, _json({"marker": self.marker}), self.now, self.now),
        )
        c.execute(
            "INSERT INTO vkpi_cost_ledger (project_id, kol_id, staff_id, cost_type, amount_cents, currency, status, incurred_at, source_ref, note, created_by_staff_id, metadata_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, kol_id, self.staff_id, "shipping", 4500, "USD", "actual", self.now, f"{self.marker}-shipping", self.marker, self.staff_id, _json({"marker": self.marker}), self.now),
        )
        c.execute(
            "INSERT INTO vkpi_sales_attributions (source_platform, source_ref, project_id, kol_id, staff_id, product_sku, revenue_cents, currency, attribution_model, confidence, occurred_at, imported_at, evidence_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("shopify", f"{self.marker}-order", project_id, kol_id, self.staff_id, sku, 129900, "USD", "last_touch", "confirmed", self.now, self.now, _json({"marker": self.marker, "order_name": self.marker}), self.now),
        )
        c.commit()
        return {"kol_id": kol_id, "project_id": project_id}

    def staff(self) -> dict[str, Any]:
        return {"id": self.staff_id, "staff_id": self.staff_id, "role": "admin", "is_owner": 1, "name": self.marker, "email": f"{self.marker}@viltrox.test"}

    def generate(self, *, ai_disabled: bool, force_offline: bool = True) -> dict[str, Any]:
        previous_disabled = os.environ.get("VKPI_WEEKLY_SUMMARY_AI_DISABLED")
        previous_force_offline = os.environ.get("VKPI_LLM_GATEWAY_FORCE_OFFLINE")
        previous_budget = os.environ.get("LLM_MONTHLY_BUDGET_USD")
        if ai_disabled:
            os.environ["VKPI_WEEKLY_SUMMARY_AI_DISABLED"] = "1"
        elif previous_disabled is not None:
            os.environ.pop("VKPI_WEEKLY_SUMMARY_AI_DISABLED", None)
        if force_offline:
            os.environ["VKPI_LLM_GATEWAY_FORCE_OFFLINE"] = "1"
            os.environ["LLM_MONTHLY_BUDGET_USD"] = "0"
        try:
            return reports.generate_weekly_report(
                period_days=7,
                staff=self.staff(),
                filters={"staff_id": self.staff_id, "smoke_marker": self.marker, "ai_disabled": ai_disabled},
                render_pdf=False,
            )
        finally:
            if previous_disabled is None:
                os.environ.pop("VKPI_WEEKLY_SUMMARY_AI_DISABLED", None)
            else:
                os.environ["VKPI_WEEKLY_SUMMARY_AI_DISABLED"] = previous_disabled
            if previous_force_offline is None:
                os.environ.pop("VKPI_LLM_GATEWAY_FORCE_OFFLINE", None)
            else:
                os.environ["VKPI_LLM_GATEWAY_FORCE_OFFLINE"] = previous_force_offline
            if previous_budget is None:
                os.environ.pop("LLM_MONTHLY_BUDGET_USD", None)
            else:
                os.environ["LLM_MONTHLY_BUDGET_USD"] = previous_budget

    def llm_status_count(self, status: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM vkpi_llm_calls WHERE created_by_staff_id=? AND purpose='vkpi_weekly_summary' AND status=?",
            (self.staff_id, status),
        ).fetchone()
        return int(row["n"] if row else 0)

    def cleanup(self) -> dict[str, int]:
        c = self.conn
        like = f"%{self.marker}%"
        report_ids = [int(r["id"]) for r in c.execute("SELECT id FROM vkpi_report_runs WHERE metadata_json LIKE ? OR summary_text LIKE ? OR report_uid LIKE ?", (like, like, like)).fetchall()]
        run_ids = [int(r["metric_run_id"]) for r in c.execute("SELECT metric_run_id FROM vkpi_report_runs WHERE (metadata_json LIKE ? OR report_uid LIKE ?) AND metric_run_id IS NOT NULL", (like, like)).fetchall()]
        file_paths = [str(r["file_path"] or "") for r in c.execute("SELECT file_path FROM vkpi_report_files WHERE report_run_id IN (%s)" % ",".join("?" for _ in report_ids), report_ids).fetchall()] if report_ids else []
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
        delete_in("vkpi_cost_ledger", "project_id", project_ids)
        c.execute("DELETE FROM vkpi_cost_ledger WHERE source_ref LIKE ? OR note LIKE ? OR metadata_json LIKE ?", (like, like, like))
        delete_in("vkpi_project_stage_events", "project_id", project_ids)
        delete_in("vkpi_kol_claims", "kol_id", kol_ids)
        delete_in("vkpi_projects", "id", project_ids)
        delete_in("kols", "id", kol_ids)
        delete_in("vkpi_llm_calls", "created_by_staff_id", staff_ids)
        delete_in("vkpi_business_audit_logs", "staff_id", staff_ids)
        delete_in("vkpi_sensitive_access_logs", "staff_id", staff_ids)
        delete_in("staff", "user_id", user_ids)
        delete_in("users", "id", user_ids)
        c.commit()
        return {
            "reports": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_report_runs WHERE metadata_json LIKE ? OR summary_text LIKE ? OR report_uid LIKE ?", (like, like, like)).fetchone()["n"]),
            "metric_sources": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_metric_sources WHERE snapshot_json LIKE ? OR evidence_ref LIKE ?", (like, like)).fetchone()["n"]),
            "llm_calls": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_llm_calls WHERE metadata_json LIKE ? OR created_by_staff_id IN (SELECT id FROM staff WHERE user_id IN (SELECT id FROM users WHERE email LIKE ? OR name LIKE ?))", (like, like, like)).fetchone()["n"]),
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
        ensure_vkpi_product_industry_schema()
        self.cleanup()
        seeded = self.seed_business_rows()

        fallback_report = self.generate(ai_disabled=True)
        fallback_summary = str(fallback_report.get("summary_text") or "")
        if "成本口径为" not in fallback_summary or "当前周期确认销售额" not in fallback_summary:
            raise AssertionError(f"fallback summary did not use the deterministic template: {fallback_summary}")
        if self.llm_status_count("disabled") < 1:
            raise AssertionError("forced fallback path did not write disabled LLM ledger row")

        gateway_report = self.generate(ai_disabled=False, force_offline=True)
        gateway_summary = str(gateway_report.get("summary_text") or "")
        if "成本口径为" not in gateway_summary or "当前周期确认销售额" not in gateway_summary:
            raise AssertionError(f"gateway fallback did not use the deterministic template: {gateway_summary}")
        if self.llm_status_count("budget_disabled") < 1:
            raise AssertionError("gateway fallback path did not write budget_disabled LLM ledger row")
        gateway_result: dict[str, Any] = {
            "forced_offline": True,
            "status": "budget_disabled",
            "reason": "external_llm_calls_are_not_run_by_default_in_smoke",
        }

        residue = self.cleanup()
        if any(residue.values()):
            raise AssertionError(f"smoke residue not cleaned: {residue}")
        return {"ok": True, "marker": self.marker, "seeded": seeded, "gateway": gateway_result, "residue": residue}


if __name__ == "__main__":
    smoke = Smoke()
    try:
        print(json.dumps(smoke.run(), ensure_ascii=False, indent=2))
    except Exception:
        residue = smoke.cleanup()
        print(json.dumps({"ok": False, "marker": smoke.marker, "cleanup_after_failure": residue}, ensure_ascii=False, indent=2))
        raise
