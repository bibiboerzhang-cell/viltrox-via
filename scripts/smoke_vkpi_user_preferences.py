#!/usr/bin/env python3
"""Smoke test for V-KPI per-staff user preferences.

Covers default creation, own update, admin update of another staff preference,
non-manager cross-staff denial, audit logging, and marker cleanup.
"""
from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from typing import Any

os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("DB_RUNTIME_BACKEND", "sqlite")
os.environ.setdefault("DATABASE_URL", "")
os.environ.setdefault("APP_ROLE", "admin-web")

from fastapi.testclient import TestClient

from app.core.security import make_token
from app.db.connection import get_conn
from app.main import app
from app.services.vkpi.schema_audit import ensure_vkpi_audit_schema
from app.services.vkpi.schema_preferences import ensure_vkpi_preferences_schema

PREFIX = "vkpi-user-pref-smoke-"


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Smoke:
    def __init__(self) -> None:
        self.marker = f"{PREFIX}{int(time.time())}"
        self.now = _now()
        self.conn = get_conn()
        self.client = TestClient(app)
        self.admin_user_id = 0
        self.admin_staff_id = 0
        self.employee_user_id = 0
        self.employee_staff_id = 0

    def seed_staff(self, *, role: str) -> tuple[int, int, str]:
        c = self.conn
        email = f"{self.marker}-{role}@viltrox.test"
        c.execute(
            "INSERT INTO users (created_at, email, password_hash, name, status, role, email_verified, avatar_url) VALUES (?,?,?,?,?,?,?,?)",
            (self.now, email, "v2:00:00", f"{self.marker}-{role}", "approved", role, 1, f"https://avatar.example/{self.marker}-{role}.png"),
        )
        user_id = int(c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"])
        c.execute(
            "INSERT INTO staff (user_id, role, permissions_json, mfa_enabled, active, invited_by, invited_at) VALUES (?,?,?,?,?,?,?)",
            (user_id, role, _json({"vkpi": "write"}), 0, 1, None, self.now),
        )
        staff_id = int(c.execute("SELECT id FROM staff WHERE user_id=?", (user_id,)).fetchone()["id"])
        c.commit()
        return user_id, staff_id, make_token(user_id, role)

    def headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def cleanup(self) -> dict[str, int]:
        c = self.conn
        like = f"%{self.marker}%"
        user_ids = [int(r["id"]) for r in c.execute("SELECT id FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchall()]
        staff_ids = [int(r["id"]) for r in c.execute("SELECT id FROM staff WHERE user_id IN (%s)" % ",".join("?" for _ in user_ids), user_ids).fetchall()] if user_ids else []

        def delete_in(table: str, column: str, ids: list[int]) -> None:
            if not ids:
                return
            ph = ",".join("?" for _ in ids)
            c.execute(f"DELETE FROM {table} WHERE {column} IN ({ph})", ids)

        delete_in("vkpi_user_preferences", "staff_id", staff_ids)
        delete_in("vkpi_settings_change_logs", "staff_id", staff_ids)
        delete_in("vkpi_business_audit_logs", "staff_id", staff_ids)
        delete_in("staff", "user_id", user_ids)
        delete_in("users", "id", user_ids)
        c.commit()
        return {
            "users": int(c.execute("SELECT COUNT(*) AS n FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchone()["n"]),
            "preferences": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_user_preferences WHERE staff_id IN (%s)" % ",".join("?" for _ in staff_ids), staff_ids).fetchone()["n"]) if staff_ids else 0,
            "settings_audit": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_settings_change_logs WHERE staff_id IN (%s)" % ",".join("?" for _ in staff_ids), staff_ids).fetchone()["n"]) if staff_ids else 0,
            "business_audit": int(c.execute("SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE staff_id IN (%s)" % ",".join("?" for _ in staff_ids), staff_ids).fetchone()["n"]) if staff_ids else 0,
        }

    def run(self) -> dict[str, Any]:
        ensure_vkpi_preferences_schema()
        ensure_vkpi_audit_schema()
        self.cleanup()
        self.admin_user_id, self.admin_staff_id, admin_token = self.seed_staff(role="admin")
        self.employee_user_id, self.employee_staff_id, employee_token = self.seed_staff(role="operator")

        default_resp = self.client.get("/api/admin/vkpi/settings/preferences", headers=self.headers(employee_token))
        if default_resp.status_code != 200:
            raise AssertionError(f"default preference read failed: {default_resp.status_code} {default_resp.text[:500]}")
        default_pref = default_resp.json().get("preference") or {}
        if int(default_pref.get("staff_id") or 0) != self.employee_staff_id:
            raise AssertionError(f"default preference scope mismatch: {default_pref}")
        if (default_pref.get("preferences") or {}).get("locale") != "zh-CN":
            raise AssertionError(f"default locale mismatch: {default_pref}")

        update_payload = {
            "landing_page": "projects",
            "date_range_default": "30d",
            "dashboard_scope_default": "self",
            "table_density": "compact",
            "rows_per_page": 33,
            "compact_mode": True,
            "right_panel_open": False,
            "preferences": {"pinned_nav": ["projects", "links"], "enabled_widgets": ["my_projects"]},
        }
        update_resp = self.client.patch("/api/admin/vkpi/settings/preferences", json=update_payload, headers=self.headers(employee_token))
        if update_resp.status_code != 200:
            raise AssertionError(f"own preference update failed: {update_resp.status_code} {update_resp.text[:500]}")
        prefs = (update_resp.json().get("preference") or {}).get("preferences") or {}
        if prefs.get("landing_page") != "projects" or prefs.get("rows_per_page") != 33 or prefs.get("compact_mode") is not True:
            raise AssertionError(f"updated preferences not persisted: {prefs}")

        denied = self.client.patch(
            "/api/admin/vkpi/settings/preferences",
            json={"staff_id": self.admin_staff_id, "landing_page": "reports"},
            headers=self.headers(employee_token),
        )
        if denied.status_code != 403:
            raise AssertionError(f"cross-staff update should be denied, got {denied.status_code}: {denied.text[:500]}")

        admin_update = self.client.patch(
            "/api/admin/vkpi/settings/preferences",
            json={"staff_id": self.employee_staff_id, "landing_page": "reports", "rows_per_page": 44},
            headers=self.headers(admin_token),
        )
        if admin_update.status_code != 200:
            raise AssertionError(f"admin preference update failed: {admin_update.status_code} {admin_update.text[:500]}")
        admin_prefs = (admin_update.json().get("preference") or {}).get("preferences") or {}
        if admin_prefs.get("landing_page") != "reports" or admin_prefs.get("rows_per_page") != 44:
            raise AssertionError(f"admin update did not persist: {admin_prefs}")

        list_resp = self.client.get("/api/admin/vkpi/settings/preferences/list", headers=self.headers(admin_token))
        if list_resp.status_code != 200 or len(list_resp.json().get("preferences") or []) < 1:
            raise AssertionError(f"manager list preferences failed: {list_resp.status_code} {list_resp.text[:500]}")
        employee_list_denied = self.client.get("/api/admin/vkpi/settings/preferences/list", headers=self.headers(employee_token))
        if employee_list_denied.status_code != 403:
            raise AssertionError(f"employee list preferences should be denied, got {employee_list_denied.status_code}")

        settings_count = int(self.conn.execute("SELECT COUNT(*) AS n FROM vkpi_settings_change_logs WHERE staff_id IN (?,?) AND change_type='user_preference'", (self.admin_staff_id, self.employee_staff_id)).fetchone()["n"])
        business_count = int(self.conn.execute("SELECT COUNT(*) AS n FROM vkpi_business_audit_logs WHERE staff_id IN (?,?) AND action_type='user_preference_update'", (self.admin_staff_id, self.employee_staff_id)).fetchone()["n"])
        if settings_count < 2 or business_count < 2:
            raise AssertionError(f"preference audit missing: settings={settings_count}, business={business_count}")

        residue = self.cleanup()
        if any(residue.values()):
            raise AssertionError(f"smoke residue not cleaned: {residue}")
        return {"ok": True, "marker": self.marker, "staff_ids": [self.admin_staff_id, self.employee_staff_id], "audit": {"settings": settings_count, "business": business_count}, "residue": residue}


if __name__ == "__main__":
    smoke = Smoke()
    try:
        print(json.dumps(smoke.run(), ensure_ascii=False, indent=2))
    except Exception:
        residue = smoke.cleanup()
        print(json.dumps({"ok": False, "marker": smoke.marker, "cleanup_after_failure": residue}, ensure_ascii=False, indent=2))
        raise
