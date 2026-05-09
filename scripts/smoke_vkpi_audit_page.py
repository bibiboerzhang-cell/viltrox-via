#!/usr/bin/env python3
"""Smoke test for V-KPI Audit overview and management-only access."""
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
from app.services.vkpi import audit
from app.services.vkpi.schema_audit import ensure_vkpi_audit_schema

BASE = "http://127.0.0.1:8102"
PREFIX = "vkpi-audit-page-smoke-"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Smoke:
    def __init__(self) -> None:
        self.marker = f"{PREFIX}{int(time.time())}"
        self.now = _now()
        self.conn = get_conn()
        self.admin_user_id = 0
        self.admin_staff_id = 0
        self.employee_user_id = 0
        self.employee_staff_id = 0
        self.admin_token = ""
        self.employee_token = ""

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        payload: dict[str, Any] | None = None,
        expect_status: int = 200,
    ) -> dict[str, Any]:
        data = None if payload is None else _json(payload).encode("utf-8")
        req = urllib.request.Request(BASE + path, data=data, method=method)
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {token}")
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
        staff_ids = [
            int(row["id"])
            for row in conn.execute(
                "SELECT s.id FROM staff s LEFT JOIN users u ON u.id=s.user_id WHERE u.email LIKE ? OR u.name LIKE ?",
                (like, like),
            ).fetchall()
        ]
        user_ids = [int(row["id"]) for row in conn.execute("SELECT id FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchall()]

        def delete_staff_rows(table: str) -> None:
            if staff_ids:
                placeholders = ",".join("?" for _ in staff_ids)
                conn.execute(f"DELETE FROM {table} WHERE staff_id IN ({placeholders})", staff_ids)

        delete_staff_rows("vkpi_sensitive_access_logs")
        delete_staff_rows("vkpi_export_logs")
        delete_staff_rows("vkpi_settings_change_logs")
        delete_staff_rows("vkpi_business_audit_logs")
        conn.execute("DELETE FROM vkpi_sensitive_access_logs WHERE metadata_json LIKE ? OR resource_id LIKE ? OR page_path LIKE ?", (like, like, like))
        conn.execute("DELETE FROM vkpi_export_logs WHERE filters_json LIKE ? OR purpose LIKE ? OR export_target LIKE ?", (like, like, like))
        conn.execute("DELETE FROM vkpi_settings_change_logs WHERE setting_key LIKE ? OR metadata_json LIKE ?", (like, like))
        conn.execute("DELETE FROM vkpi_business_audit_logs WHERE target_id LIKE ? OR detail LIKE ? OR metadata_json LIKE ?", (like, like, like))
        if user_ids:
            placeholders = ",".join("?" for _ in user_ids)
            conn.execute(f"DELETE FROM staff WHERE user_id IN ({placeholders})", user_ids)
            conn.execute(f"DELETE FROM users WHERE id IN ({placeholders})", user_ids)
        conn.commit()
        return {
            "users": int(conn.execute("SELECT COUNT(*) AS n FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchone()["n"]),
            "staff": int(conn.execute("SELECT COUNT(*) AS n FROM staff s LEFT JOIN users u ON u.id=s.user_id WHERE u.email LIKE ? OR u.name LIKE ?", (like, like)).fetchone()["n"]),
            "sensitive": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_sensitive_access_logs WHERE metadata_json LIKE ? OR resource_id LIKE ? OR page_path LIKE ?", (like, like, like)).fetchone()["n"]),
            "exports": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_export_logs WHERE filters_json LIKE ? OR purpose LIKE ? OR export_target LIKE ?", (like, like, like)).fetchone()["n"]),
            "settings": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_settings_change_logs WHERE setting_key LIKE ? OR metadata_json LIKE ?", (like, like)).fetchone()["n"]),
            "business": int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE target_id LIKE ? OR detail LIKE ? OR metadata_json LIKE ?", (like, like, like)).fetchone()["n"]),
        }

    def seed_identities(self) -> None:
        conn = self.conn
        for suffix, role in (("admin", "admin"), ("employee", "employee")):
            permission_level = "admin" if suffix == "admin" else "write"
            conn.execute(
                "INSERT INTO users (email, name, role, status, created_at, password_hash, email_verified) VALUES (?,?,?,?,?,?,?)",
                (f"{self.marker}-{suffix}@example.com", f"{self.marker}-{suffix}", role, "active", self.now, "v2:00:00", 1),
            )
            user_id = int(conn.execute("SELECT id FROM users WHERE email=?", (f"{self.marker}-{suffix}@example.com",)).fetchone()["id"])
            conn.execute(
                "INSERT INTO staff (user_id, role, permissions_json, active, invited_at, accepted_at, last_active_at) VALUES (?,?,?,?,?,?,?)",
                (user_id, role, _json({"vkpi": permission_level}), 1, self.now, self.now, self.now),
            )
            staff_id = int(conn.execute("SELECT id FROM staff WHERE user_id=?", (user_id,)).fetchone()["id"])
            if suffix == "admin":
                self.admin_user_id = user_id
                self.admin_staff_id = staff_id
                self.admin_token = make_token(user_id, "admin")
            else:
                self.employee_user_id = user_id
                self.employee_staff_id = staff_id
                self.employee_token = make_token(user_id, "employee")
        conn.commit()

    def seed_audit_events(self) -> None:
        audit.log_business_event(
            staff_id=self.admin_staff_id,
            action_type="project_stage_change",
            target_type="project",
            target_id=self.marker,
            detail=f"{self.marker} business event",
            metadata={"marker": self.marker},
        )
        audit.log_sensitive_access(
            staff_id=self.admin_staff_id,
            action_type="view_financial",
            resource_type="cost",
            resource_id=self.marker,
            page_path=f"/api/admin/vkpi/costs?marker={self.marker}",
            ip="127.0.0.1",
            user_agent="vkpi-audit-smoke",
            metadata={"marker": self.marker},
        )
        audit.log_export(
            staff_id=self.admin_staff_id,
            export_kind="csv",
            export_target=self.marker,
            filters={"marker": self.marker},
            row_count=3,
            purpose=f"{self.marker} export",
            contains_financial=True,
            metric_keys=["gmv", "cost"],
        )
        audit.log_settings_change(
            staff_id=self.admin_staff_id,
            change_type="provider_probe",
            setting_key=f"{self.marker}.provider",
            old_value_redacted="not_configured",
            new_value_redacted="working",
            metadata={"marker": self.marker},
        )

    def run(self) -> dict[str, Any]:
        ensure_vkpi_audit_schema()
        self.cleanup()
        categories: set[str | None] = set()
        events: list[dict[str, Any]] = []
        summary: dict[str, Any] = {}
        denied: dict[str, Any] = {}
        try:
            self.seed_identities()
            self.seed_audit_events()
            self.request("GET", f"/api/admin/vkpi/kpi-ledger?staff_id={self.admin_staff_id}&limit=5", token=self.admin_token)
            sensitive_actions = {
                str(row["action_type"])
                for row in self.conn.execute(
                    "SELECT action_type FROM vkpi_sensitive_access_logs WHERE staff_id=?",
                    (self.admin_staff_id,),
                ).fetchall()
            }
            business_actions = {
                str(row["action_type"])
                for row in self.conn.execute(
                    "SELECT action_type FROM vkpi_business_audit_logs WHERE staff_id=?",
                    (self.admin_staff_id,),
                ).fetchall()
            }
            assert "view_kpi_ledger" in sensitive_actions, sensitive_actions
            assert "kpi_ledger_view" in business_actions, business_actions
            overview = self.request("GET", "/api/admin/vkpi/audit/overview?limit=100&days=7", token=self.admin_token)
            events = overview.get("events") or []
            categories = {row.get("event_category") for row in events if self.marker in json.dumps(row, ensure_ascii=False)}
            expected = {"business", "sensitive_access", "export", "settings_change"}
            missing = sorted(expected - categories)
            assert not missing, {"missing_categories": missing, "categories": sorted(categories), "events": events[:5]}
            summary = overview.get("summary") or {}
            assert int(summary.get("business_event_count") or 0) >= 1, summary
            assert int(summary.get("sensitive_access_count") or 0) >= 1, summary
            assert int(summary.get("export_count") or 0) >= 1, summary
            assert int(summary.get("settings_change_count") or 0) >= 1, summary
            filtered = self.request("GET", "/api/admin/vkpi/audit/overview?limit=100&days=7&event_category=business", token=self.admin_token)
            assert all(row.get("event_category") == "business" for row in filtered.get("events", [])), filtered
            denied = self.request("GET", "/api/admin/vkpi/audit/overview?limit=10", token=self.employee_token, expect_status=403)
        finally:
            cleanup_counts = self.cleanup()
        assert all(value == 0 for value in cleanup_counts.values()), cleanup_counts
        return {
            "marker": self.marker,
            "verified_categories": sorted(str(category) for category in categories if category),
            "event_count": len(events),
            "summary": summary,
            "kpi_audit_actions": {
                "sensitive": sorted(sensitive_actions),
                "business": sorted(business_actions),
            },
            "employee_denied": denied.get("status_code"),
            "cleanup": cleanup_counts,
        }


def main() -> None:
    smoke = Smoke()
    result = smoke.run()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
