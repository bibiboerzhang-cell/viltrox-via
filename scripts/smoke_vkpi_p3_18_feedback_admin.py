#!/usr/bin/env python3
"""P3.18 feedback admin smoke.

Validates the management path added after the user-facing P3.17 feedback widget:
- real HTTP create feedback
- real HTTP admin list/filter
- real HTTP status transition
- database row reflects the admin action
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import json
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.permissions import default_permissions_for_role
from app.core.security import make_token
from app.db.connection import get_conn


ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8102"
PREFIX = "vkpi-p3-18-feedback-admin-"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _request_json(path: str, *, method: str = "GET", token: str = "", payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
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
        self.marker = f"{PREFIX}{int(time.time())}"
        self.now = _now()
        self.conn = get_conn()
        self.user_id = 0
        self.token = ""
        self.uids: list[str] = []

    def cleanup(self) -> None:
        try:
            self.conn.execute("DELETE FROM vkpi_team_feedback WHERE title LIKE ?", (f"{self.marker}%",))
        except Exception:
            pass
        try:
            placeholders = ",".join("?" for _ in self.uids)
            if placeholders:
                self.conn.execute(
                    f"DELETE FROM vkpi_business_audit_logs WHERE target_type='team_feedback' AND target_id IN ({placeholders})",
                    self.uids,
                )
        except Exception:
            pass
        rows = self.conn.execute("SELECT id FROM users WHERE email LIKE ? OR name LIKE ?", (f"{self.marker}%", f"{self.marker}%")).fetchall()
        user_ids = [int(row["id"]) for row in rows]
        if user_ids:
            placeholders = ",".join("?" for _ in user_ids)
            self.conn.execute(f"DELETE FROM staff WHERE user_id IN ({placeholders})", user_ids)
            self.conn.execute(f"DELETE FROM users WHERE id IN ({placeholders})", user_ids)
        self.conn.commit()

    def seed_admin(self) -> None:
        email = f"{self.marker}@viltrox.com"
        permissions = default_permissions_for_role("admin", owner=True)
        self.conn.execute(
            """
            INSERT INTO users (
                created_at, email, password_hash, name, status, role,
                email_verified, avatar_url
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                self.now,
                email,
                "v2:00:00",
                self.marker,
                "approved",
                "admin",
                1,
                f"https://avatar.example/{self.marker}.png",
            ),
        )
        self.user_id = int(self.conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"])
        staff_cols = {str(row["name"]) for row in self.conn.execute("PRAGMA table_info(staff)").fetchall()}
        cols = ["user_id", "role", "permissions_json", "mfa_enabled", "active", "invited_by", "invited_at"]
        values: list[Any] = [self.user_id, "admin", _json(permissions), 0, 1, None, self.now]
        if "is_owner" in staff_cols:
            cols.append("is_owner")
            values.append(1)
        if "email_domain_verified" in staff_cols:
            cols.append("email_domain_verified")
            values.append(1)
        placeholders = ",".join("?" for _ in cols)
        self.conn.execute(f"INSERT INTO staff ({', '.join(cols)}) VALUES ({placeholders})", values)
        self.conn.commit()
        self.token = make_token(self.user_id, "admin")

    def create_feedback(self, index: int, severity: str) -> str:
        status, created = _request_json(
            "/api/admin/vkpi/feedback",
            method="POST",
            token=self.token,
            payload={
                "feedbackType": "button_issue" if index == 1 else "suggestion",
                "severity": severity,
                "pagePath": "#settings" if index == 1 else "#dataAnalysis",
                "title": f"{self.marker} admin item {index}",
                "detail": "smoke: feedback admin triage should load, filter, and update this row.",
                "metadata": {"smoke": True, "round": "P3.18", "index": index},
            },
        )
        assert status == 200, {"status": status, "payload": created}
        uid = str((created.get("feedback") or {}).get("uid") or "")
        assert uid.startswith("fb_"), created
        self.uids.append(uid)
        return uid

    def run(self) -> dict[str, Any]:
        self.cleanup()
        self.seed_admin()
        first_uid = self.create_feedback(1, "high")
        second_uid = self.create_feedback(2, "low")

        status, open_rows = _request_json("/api/admin/vkpi/feedback?status=open&limit=100", token=self.token)
        assert status == 200, {"status": status, "payload": open_rows}
        open_uids = {str(item.get("uid")) for item in (open_rows.get("feedback") or [])}
        assert first_uid in open_uids and second_uid in open_uids, open_rows

        status, updated = _request_json(
            f"/api/admin/vkpi/feedback/{first_uid}",
            method="PATCH",
            token=self.token,
            payload={"status": "in_progress"},
        )
        assert status == 200, {"status": status, "payload": updated}
        assert (updated.get("feedback") or {}).get("status") == "in_progress", updated

        row = self.conn.execute("SELECT status FROM vkpi_team_feedback WHERE uid=?", (first_uid,)).fetchone()
        assert row and str(row["status"]) == "in_progress", dict(row or {})

        status, filtered = _request_json("/api/admin/vkpi/feedback?status=in_progress&limit=100", token=self.token)
        assert status == 200, {"status": status, "payload": filtered}
        filtered_uids = {str(item.get("uid")) for item in (filtered.get("feedback") or [])}
        assert first_uid in filtered_uids, filtered
        assert second_uid not in filtered_uids, filtered

        return {"updated_uid": first_uid, "open_uid": second_uid, "in_progress_count": len(filtered_uids)}


def main() -> int:
    smoke = Smoke()
    try:
        result = smoke.run()
        stdout_out("VKPI_P3_18_FEEDBACK_ADMIN_SMOKE_OK", json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        smoke.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
