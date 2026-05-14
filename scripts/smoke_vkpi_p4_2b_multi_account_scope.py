#!/usr/bin/env python3
"""P4.2B multi-account role/scope E2E smoke.

Seeds one manager and two employee accounts, creates employee-owned KOL/project
records through HTTP, then verifies:
- employees only see their own scoped KOL/project rows;
- employee A cannot open employee B project/KOL detail;
- manager can see all rows and intentionally filter by staff_id.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from _smoke_seed import cleanup_admin, seed_admin
from app.core.security import make_token
from app.db.connection import get_conn


BASE = "http://127.0.0.1:8102"
PREFIX = "p4-2b-scope"


def _request_json(
    path: str,
    *,
    method: str = "GET",
    token: str = "",
    payload: dict[str, Any] | None = None,
    timeout: int = 25,
) -> tuple[int, dict[str, Any]]:
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
        req.add_header("X-Requested-With", "XMLHttpRequest")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return int(resp.status), json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="ignore")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw[:500]}
        return int(exc.code), payload


def _ids(rows: list[dict[str, Any]]) -> set[int]:
    return {int(row.get("id") or 0) for row in rows if int(row.get("id") or 0)}


def _query(params: dict[str, Any]) -> str:
    return urllib.parse.urlencode({key: value for key, value in params.items() if value is not None})


class Smoke:
    def __init__(self) -> None:
        self.marker = f"{PREFIX}-{int(time.time())}"
        self.conn = get_conn()
        self.manager_user_id = 0
        self.manager_staff_id = 0
        self.employee_a_user_id = 0
        self.employee_a_staff_id = 0
        self.employee_b_user_id = 0
        self.employee_b_staff_id = 0
        self.manager_token = ""
        self.employee_a_token = ""
        self.employee_b_token = ""
        self.kol_a_id = 0
        self.kol_b_id = 0
        self.project_a_id = 0
        self.project_b_id = 0

    def cleanup(self) -> None:
        try:
            project_rows = self.conn.execute(
                "SELECT id FROM vkpi_projects WHERE project_uid LIKE ? OR project_name LIKE ?",
                (f"{self.marker}%", f"{self.marker}%"),
            ).fetchall()
            project_ids = [int(row["id"]) for row in project_rows]
            kol_rows = self.conn.execute(
                "SELECT id FROM kols WHERE channel_name LIKE ? OR project_name LIKE ?",
                (f"{self.marker}%", f"{self.marker}%"),
            ).fetchall()
            kol_ids = [int(row["id"]) for row in kol_rows]
            if project_ids:
                placeholders = ",".join("?" for _ in project_ids)
                self.conn.execute(f"DELETE FROM vkpi_project_stage_events WHERE project_id IN ({placeholders})", project_ids)
                self.conn.execute(f"DELETE FROM vkpi_kol_claims WHERE project_id IN ({placeholders})", project_ids)
                self.conn.execute(f"DELETE FROM vkpi_projects WHERE id IN ({placeholders})", project_ids)
            if kol_ids:
                placeholders = ",".join("?" for _ in kol_ids)
                self.conn.execute(f"DELETE FROM vkpi_kol_claims WHERE kol_id IN ({placeholders})", kol_ids)
                self.conn.execute(f"DELETE FROM kols WHERE id IN ({placeholders})", kol_ids)
            self.conn.commit()
        except Exception:
            pass
        cleanup_admin(self.conn, user_id=self.employee_b_user_id, staff_id=self.employee_b_staff_id)
        cleanup_admin(self.conn, user_id=self.employee_a_user_id, staff_id=self.employee_a_staff_id)
        cleanup_admin(self.conn, user_id=self.manager_user_id, staff_id=self.manager_staff_id)

    def seed_accounts(self) -> None:
        self.manager_user_id, self.manager_staff_id = seed_admin(
            self.conn,
            marker=self.marker,
            suffix="manager",
            role="manager",
            vkpi_permission="write",
            is_owner=False,
        )
        self.employee_a_user_id, self.employee_a_staff_id = seed_admin(
            self.conn,
            marker=self.marker,
            suffix="employee-a",
            role="employee",
            vkpi_permission="write",
            is_owner=False,
        )
        self.employee_b_user_id, self.employee_b_staff_id = seed_admin(
            self.conn,
            marker=self.marker,
            suffix="employee-b",
            role="employee",
            vkpi_permission="write",
            is_owner=False,
        )
        self.manager_token = make_token(self.manager_user_id, "manager")
        self.employee_a_token = make_token(self.employee_a_user_id, "employee")
        self.employee_b_token = make_token(self.employee_b_user_id, "employee")

    def create_employee_fixture(self, suffix: str, token: str) -> tuple[int, int]:
        handle = f"{self.marker}-{suffix}"
        status, lookup = _request_json(
            "/api/admin/vkpi/kols/lookup",
            method="POST",
            token=token,
            payload={
                "platform": "instagram",
                "handle": handle,
                "create_if_missing": True,
                "follower_count": 12345,
                "avg_views": 678,
                "profile_url": f"https://www.instagram.com/{handle}/",
                "project_name": f"{self.marker} kol {suffix}",
            },
        )
        assert status == 200, {"status": status, "payload": lookup}
        kol_id = int((lookup.get("kol") or {}).get("id") or 0)
        assert kol_id > 0, lookup

        status, project = _request_json(
            "/api/admin/vkpi/projects",
            method="POST",
            token=token,
            payload={
                "project_uid": f"{self.marker}-{suffix}".upper(),
                "project_name": f"{self.marker} project {suffix}",
                "kol_id": kol_id,
                "platform": "instagram",
                "product_sku": "P4-SCOPE",
                "product_name": "P4 Scope Lens",
                "source_type": "smoke",
            },
        )
        assert status == 200, {"status": status, "payload": project}
        project_id = int(project.get("id") or 0)
        assert project_id > 0, project
        return kol_id, project_id

    def assert_project_scope(self) -> dict[str, Any]:
        status, a_projects = _request_json("/api/admin/vkpi/projects?limit=200", token=self.employee_a_token)
        assert status == 200, {"status": status, "payload": a_projects}
        a_ids = _ids(a_projects.get("projects") or [])
        assert self.project_a_id in a_ids, a_projects
        assert self.project_b_id not in a_ids, a_projects
        assert (a_projects.get("scope") or {}).get("scope_mode") == "own", a_projects

        status, a_requested_b = _request_json(
            f"/api/admin/vkpi/projects?{_query({'limit': 200, 'staff_id': self.employee_b_staff_id})}",
            token=self.employee_a_token,
        )
        assert status == 200, {"status": status, "payload": a_requested_b}
        requested_ids = _ids(a_requested_b.get("projects") or [])
        assert self.project_a_id in requested_ids, a_requested_b
        assert self.project_b_id not in requested_ids, a_requested_b
        assert (a_requested_b.get("scope") or {}).get("effective_staff_id") == self.employee_a_staff_id, a_requested_b

        status, b_detail = _request_json(f"/api/admin/vkpi/projects/{self.project_b_id}", token=self.employee_a_token)
        assert status == 403, {"status": status, "payload": b_detail}

        status, manager_projects = _request_json("/api/admin/vkpi/projects?limit=200", token=self.manager_token)
        assert status == 200, {"status": status, "payload": manager_projects}
        manager_ids = _ids(manager_projects.get("projects") or [])
        assert {self.project_a_id, self.project_b_id}.issubset(manager_ids), manager_projects
        assert (manager_projects.get("scope") or {}).get("scope_mode") == "all", manager_projects

        status, manager_a_only = _request_json(
            f"/api/admin/vkpi/projects?{_query({'limit': 200, 'staff_id': self.employee_a_staff_id})}",
            token=self.manager_token,
        )
        assert status == 200, {"status": status, "payload": manager_a_only}
        manager_a_ids = _ids(manager_a_only.get("projects") or [])
        assert self.project_a_id in manager_a_ids, manager_a_only
        assert self.project_b_id not in manager_a_ids, manager_a_only
        assert (manager_a_only.get("scope") or {}).get("scope_mode") == "requested_staff", manager_a_only
        return {
            "employee_a_visible_projects": sorted(a_ids),
            "manager_visible_test_projects": sorted({self.project_a_id, self.project_b_id}),
        }

    def assert_kol_scope(self) -> dict[str, Any]:
        search = self.marker
        status, a_kols = _request_json(f"/api/admin/vkpi/kols?{_query({'search': search, 'limit': 200})}", token=self.employee_a_token)
        assert status == 200, {"status": status, "payload": a_kols}
        a_ids = _ids(a_kols.get("kols") or [])
        assert self.kol_a_id in a_ids, a_kols
        assert self.kol_b_id not in a_ids, a_kols
        assert (a_kols.get("scope") or {}).get("scope_mode") == "own", a_kols

        status, b_profile = _request_json(f"/api/admin/vkpi/kols/{self.kol_b_id}/profile", token=self.employee_a_token)
        assert status == 403, {"status": status, "payload": b_profile}

        status, manager_kols = _request_json(f"/api/admin/vkpi/kols?{_query({'search': search, 'limit': 200})}", token=self.manager_token)
        assert status == 200, {"status": status, "payload": manager_kols}
        manager_ids = _ids(manager_kols.get("kols") or [])
        assert {self.kol_a_id, self.kol_b_id}.issubset(manager_ids), manager_kols

        status, manager_b_only = _request_json(
            f"/api/admin/vkpi/kols?{_query({'search': search, 'limit': 200, 'staff_id': self.employee_b_staff_id})}",
            token=self.manager_token,
        )
        assert status == 200, {"status": status, "payload": manager_b_only}
        manager_b_ids = _ids(manager_b_only.get("kols") or [])
        assert self.kol_b_id in manager_b_ids, manager_b_only
        assert self.kol_a_id not in manager_b_ids, manager_b_only
        return {
            "employee_a_visible_kols": sorted(a_ids),
            "manager_visible_test_kols": sorted({self.kol_a_id, self.kol_b_id}),
        }

    def run(self) -> dict[str, Any]:
        self.cleanup()
        self.seed_accounts()
        self.kol_a_id, self.project_a_id = self.create_employee_fixture("employee-a", self.employee_a_token)
        self.kol_b_id, self.project_b_id = self.create_employee_fixture("employee-b", self.employee_b_token)
        project_scope = self.assert_project_scope()
        kol_scope = self.assert_kol_scope()
        return {
            "manager_staff_id": self.manager_staff_id,
            "employee_a_staff_id": self.employee_a_staff_id,
            "employee_b_staff_id": self.employee_b_staff_id,
            "project_scope": project_scope,
            "kol_scope": kol_scope,
        }


def main() -> int:
    smoke = Smoke()
    try:
        result = smoke.run()
        print("VKPI_P4_2B_MULTI_ACCOUNT_SCOPE_SMOKE_OK", json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        smoke.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
