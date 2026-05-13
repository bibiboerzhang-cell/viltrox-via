#!/usr/bin/env python3
"""P3.17 team feedback loop smoke.

Validates the real browser-facing API path:
- POST /api/admin/vkpi/feedback creates a DB row
- GET /api/admin/vkpi/feedback lists it for admin triage
- PATCH /api/admin/vkpi/feedback/{uid} updates status
- business audit receives create/update events
"""
from __future__ import annotations

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
PREFIX = "vkpi-p3-17-feedback-"


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
        self.feedback_uid = ""

    def cleanup(self) -> None:
        try:
            self.conn.execute("DELETE FROM vkpi_team_feedback WHERE title LIKE ? OR uid=?", (f"{self.marker}%", self.feedback_uid))
        except Exception:
            pass
        try:
            self.conn.execute(
                "DELETE FROM vkpi_business_audit_logs WHERE target_type='team_feedback' AND (target_id=? OR detail LIKE ?)",
                (self.feedback_uid, f"{self.marker}%"),
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

    def run(self) -> dict[str, Any]:
        self.cleanup()
        self.seed_admin()

        status, created = _request_json(
            "/api/admin/vkpi/feedback",
            method="POST",
            token=self.token,
            payload={
                "feedbackType": "button_issue",
                "severity": "high",
                "pagePath": "#dataAnalysis",
                "title": f"{self.marker} 查看全部按钮无反应",
                "detail": "smoke: user clicked view all on media list and expected a full list drawer.",
                "metadata": {"smoke": True, "round": "P3.17"},
            },
        )
        assert status == 200, {"status": status, "payload": created}
        feedback = created.get("feedback") or {}
        self.feedback_uid = str(feedback.get("uid") or "")
        assert self.feedback_uid.startswith("fb_"), feedback
        assert feedback.get("status") == "open", feedback
        assert feedback.get("feedback_type") == "button_issue", feedback

        row = self.conn.execute("SELECT * FROM vkpi_team_feedback WHERE uid=?", (self.feedback_uid,)).fetchone()
        assert row, "feedback row missing"
        assert dict(row).get("title", "").startswith(self.marker), dict(row)

        status, listed = _request_json("/api/admin/vkpi/feedback?status=open&limit=20", token=self.token)
        assert status == 200, {"status": status, "payload": listed}
        rows = listed.get("feedback") or []
        assert any(str(item.get("uid")) == self.feedback_uid for item in rows), listed

        status, updated = _request_json(
            f"/api/admin/vkpi/feedback/{self.feedback_uid}",
            method="PATCH",
            token=self.token,
            payload={"status": "triaged"},
        )
        assert status == 200, {"status": status, "payload": updated}
        assert (updated.get("feedback") or {}).get("status") == "triaged", updated

        audit_rows = self.conn.execute(
            "SELECT action_type FROM vkpi_business_audit_logs WHERE target_type='team_feedback' AND target_id=? ORDER BY id",
            (self.feedback_uid,),
        ).fetchall()
        actions = [str(row["action_type"]) for row in audit_rows]
        assert "team_feedback_create" in actions, actions
        assert "team_feedback_status_update" in actions, actions

        return {"uid": self.feedback_uid, "audit_actions": actions}


def main() -> int:
    smoke = Smoke()
    try:
        result = smoke.run()
        print("VKPI_P3_17_FEEDBACK_LOOP_SMOKE_OK", json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        smoke.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
