#!/usr/bin/env python3
"""P4.1 real team observation readiness smoke.

This smoke validates the operational gate before real staff observation:
- runtime /health identity is current enough to trust;
- feedback submit/list/triage works through HTTP;
- audit records are emitted for feedback create/update;
- staff readiness counts are visible and machine-readable.

It intentionally does not require 3+ real staff accounts to pass. Account
provisioning is an observation rollout condition, not a code correctness check.
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from _smoke_seed import cleanup_admin, seed_admin
from app.core.security import make_token
from app.db.connection import get_conn
from app.domains.feedback import team_feedback


ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8102"
PREFIX = "vkpi-p4-1-observation-"


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


def _repo_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return ""


def _count_staff(conn) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT
            s.id AS staff_id,
            COALESCE(s.role, '') AS role,
            COALESCE(s.active, 1) AS active,
            COALESCE(s.is_owner, 0) AS is_owner,
            COALESCE(u.email, '') AS email,
            COALESCE(u.status, '') AS user_status
        FROM staff s
        LEFT JOIN users u ON u.id = s.user_id
        """
    ).fetchall()
    active_rows = [
        dict(row)
        for row in rows
        if int(dict(row).get("active") or 0) == 1 and str(dict(row).get("user_status") or "approved") != "blocked"
    ]
    admin_roles = {"owner", "admin", "manager", "lead", "marketing_lead", "marketing-manager", "marketing_manager"}
    real_rows = [
        row
        for row in active_rows
        if not str(row.get("email") or "").endswith("@example.com")
        and not str(row.get("email") or "").startswith(PREFIX)
        and "smoke" not in str(row.get("email") or "").lower()
    ]
    real_admin_rows = [
        row
        for row in real_rows
        if int(row.get("is_owner") or 0) == 1 or str(row.get("role") or "").lower() in admin_roles
    ]
    return {
        "total_active_staff": len(active_rows),
        "real_staff_candidates": len(real_rows),
        "real_admin_staff": len(real_admin_rows),
        "real_employee_staff": max(0, len(real_rows) - len(real_admin_rows)),
        "recommended_real_staff_min": 3,
        "staff_ready_for_observation": len(real_rows) >= 3 and len(real_admin_rows) >= 1,
    }


class Smoke:
    def __init__(self) -> None:
        self.marker = f"{PREFIX}{int(time.time())}"
        self.conn = get_conn()
        self.admin_user_id = 0
        self.admin_staff_id = 0
        self.writer_user_id = 0
        self.writer_staff_id = 0
        self.admin_token = ""
        self.writer_token = ""
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
        cleanup_admin(self.conn, user_id=self.writer_user_id, staff_id=self.writer_staff_id)
        cleanup_admin(self.conn, user_id=self.admin_user_id, staff_id=self.admin_staff_id)

    def seed(self) -> None:
        self.admin_user_id, self.admin_staff_id = seed_admin(
            self.conn,
            marker=self.marker,
            suffix="admin",
            role="admin",
            vkpi_permission="admin",
            is_owner=True,
        )
        self.writer_user_id, self.writer_staff_id = seed_admin(
            self.conn,
            marker=self.marker,
            suffix="writer",
            role="employee",
            vkpi_permission="write",
            is_owner=False,
        )
        self.admin_token = make_token(self.admin_user_id, "admin")
        self.writer_token = make_token(self.writer_user_id, "employee")

    def check_health(self) -> dict[str, Any]:
        status, health = _request_json("/health")
        assert status == 200, {"status": status, "payload": health}
        build = health.get("build") or {}
        server_sha = str(build.get("git_sha") or "")
        assert server_sha not in {"", "unknown"}, build
        repo_sha = _repo_sha()
        if repo_sha:
            assert server_sha.startswith(repo_sha[:12]), {"repo_sha": repo_sha, "server_sha": server_sha}
        if (ROOT / "frontend/dist/build-info.json").exists():
            assert build.get("client_build_source") == "frontend_dist", build
            assert build.get("client_matches_server") is True, build
        return {
            "server_sha": server_sha[:8],
            "repo_sha": repo_sha[:8] if repo_sha else "",
            "client_matches_server": bool(build.get("client_matches_server")),
        }

    def check_feedback_flow(self) -> dict[str, Any]:
        team_feedback.ensure_team_feedback_schema()
        status, created = _request_json(
            "/api/admin/vkpi/feedback",
            method="POST",
            token=self.writer_token,
            payload={
                "feedbackType": "button_issue",
                "severity": "medium",
                "pagePath": "#p4-observation",
                "title": f"{self.marker} staff observation feedback",
                "detail": "smoke: P4.1 staff feedback submission and admin triage should work.",
                "metadata": {"smoke": True, "round": "P4.1"},
            },
        )
        assert status == 200, {"status": status, "payload": created}
        self.feedback_uid = str((created.get("feedback") or {}).get("uid") or "")
        assert self.feedback_uid.startswith("fb_"), created

        status, listed = _request_json("/api/admin/vkpi/feedback?status=open&limit=100", token=self.admin_token)
        assert status == 200, {"status": status, "payload": listed}
        assert any(str(row.get("uid")) == self.feedback_uid for row in listed.get("feedback") or []), listed

        status, updated = _request_json(
            f"/api/admin/vkpi/feedback/{self.feedback_uid}",
            method="PATCH",
            token=self.admin_token,
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
        return {"feedback_uid": self.feedback_uid, "audit_actions": actions}

    def run(self) -> dict[str, Any]:
        self.cleanup()
        self.seed()
        health = self.check_health()
        feedback = self.check_feedback_flow()
        staff = _count_staff(self.conn)
        assert staff["total_active_staff"] >= 2, staff
        return {"health": health, "feedback": feedback, "staff": staff}


def main() -> int:
    smoke = Smoke()
    try:
        result = smoke.run()
        stdout_out("VKPI_P4_1_OBSERVATION_READINESS_SMOKE_OK", json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        smoke.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
