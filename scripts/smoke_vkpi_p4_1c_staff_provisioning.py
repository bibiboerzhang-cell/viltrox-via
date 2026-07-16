#!/usr/bin/env python3
"""P4.1C staff provisioning smoke.

Validates that the provisioning utility can create real-style staff accounts,
that those accounts can login through /api/auth/login, and that /api/auth/me
returns staff context and permissions. Test rows are cleaned at the end.
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import json
import time
import urllib.error
import urllib.request
from typing import Any

from app.db.connection import get_conn
from vkpi_provision_observation_staff import StaffRecord, provision_records


BASE = "http://127.0.0.1:8102"
PREFIX = "p4-1c-provision"


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


class Smoke:
    def __init__(self) -> None:
        self.marker = f"{PREFIX}-{int(time.time())}"
        self.password = f"VkpiP4!{int(time.time())}"
        self.conn = get_conn()
        self.emails = [
            f"{self.marker}-manager@viltrox.com",
            f"{self.marker}-employee-a@viltrox.com",
            f"{self.marker}-employee-b@viltrox.com",
        ]

    def cleanup(self) -> None:
        user_ids: list[int] = []
        for email in self.emails:
            rows = self.conn.execute("SELECT id FROM users WHERE lower(email)=?", (email.lower(),)).fetchall()
            user_ids.extend(int(row["id"]) for row in rows)
        if user_ids:
            placeholders = ",".join("?" for _ in user_ids)
            self.conn.execute(f"DELETE FROM staff WHERE user_id IN ({placeholders})", user_ids)
            self.conn.execute(f"DELETE FROM users WHERE id IN ({placeholders})", user_ids)
            self.conn.commit()

    def check_login(self, email: str, expected_role: str) -> dict[str, Any]:
        status, login = _request_json(
            "/api/auth/login",
            method="POST",
            payload={"email": email, "password": self.password},
        )
        assert status == 200, {"email": email, "status": status, "payload": login}
        assert login.get("status") == "success", login
        token = str(login.get("token") or "")
        assert token, login
        user = login.get("user") or {}
        assert int(user.get("staff_id") or 0) > 0, user
        assert str(user.get("role") or "").lower() == expected_role, user
        status, me = _request_json("/api/auth/me", token=token)
        assert status == 200, {"email": email, "status": status, "payload": me}
        me_user = me.get("user") or me
        assert int(me_user.get("staff_id") or 0) > 0, me
        permissions = me_user.get("permissions") or {}
        assert permissions.get("vkpi") in {"write", "admin"}, permissions
        return {"email": email, "role": expected_role, "staff_id": int(me_user.get("staff_id") or 0)}

    def run(self) -> dict[str, Any]:
        self.cleanup()
        records = [
            StaffRecord(email=self.emails[0], name="P4 Manager", role="manager", initial_password=self.password),
            StaffRecord(email=self.emails[1], name="P4 Employee A", role="employee", initial_password=self.password),
            StaffRecord(email=self.emails[2], name="P4 Employee B", role="employee", initial_password=self.password),
        ]
        dry = provision_records(records, apply=False, allow_external=False)
        assert dry["mode"] == "dry_run", dry
        applied = provision_records(records, apply=True, allow_external=False)
        assert applied["count"] == 3, applied
        rows = self.conn.execute(
            """
            SELECT u.email, u.role AS user_role, s.id AS staff_id, s.role AS staff_role, s.active, s.permissions_json
            FROM users u
            JOIN staff s ON s.user_id = u.id
            WHERE lower(u.email) LIKE ?
            ORDER BY u.email
            """,
            (f"{self.marker}-%",),
        ).fetchall()
        assert len(rows) == 3, [dict(row) for row in rows]
        assert all(int(row["active"] or 0) == 1 for row in rows), [dict(row) for row in rows]
        logins = [
            self.check_login(self.emails[0], "manager"),
            self.check_login(self.emails[1], "employee"),
            self.check_login(self.emails[2], "employee"),
        ]
        return {"provisioned": len(rows), "logins": logins}


def main() -> int:
    smoke = Smoke()
    try:
        result = smoke.run()
        stdout_out("VKPI_P4_1C_STAFF_PROVISIONING_SMOKE_OK", json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        smoke.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
