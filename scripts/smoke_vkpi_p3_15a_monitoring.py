#!/usr/bin/env python3
"""P3.15A production monitoring smoke.

This smoke keeps the monitoring scope small and real:
- public /health exposes build metadata without auth
- deep /health is blocked without auth
- deep /health works for an admin staff token and includes DB/queue probes
- /api/admin/runtime/metrics returns runtime request and provider telemetry

Run through scripts/run_smoke.sh so DB/runtime env matches the live backend.
"""
from __future__ import annotations

import json
import subprocess
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
PREFIX = "vkpi-p3-15a-monitoring-"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _repo_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return ""


def _request_json(path: str, *, token: str = "", timeout: int = 15) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(BASE + path, method="GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return int(resp.status), json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            payload = {"raw": body[:500]}
        return int(exc.code), payload


class Smoke:
    def __init__(self) -> None:
        self.marker = f"{PREFIX}{int(time.time())}"
        self.now = _now()
        self.conn = get_conn()
        self.user_id = 0
        self.token = ""

    def cleanup(self) -> None:
        like = f"{self.marker}%"
        rows = self.conn.execute("SELECT id FROM users WHERE email LIKE ? OR name LIKE ?", (like, like)).fetchall()
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

        status, shallow = _request_json("/health")
        assert status == 200, f"/health failed: {status} {shallow}"
        assert shallow.get("status") == "ok", shallow
        build = shallow.get("build") or {}
        assert str(build.get("git_sha") or "") not in {"", "unknown"}, build
        assert str(build.get("git_short_sha") or "") not in {"", "unknown"}, build

        server_sha = str(build.get("git_sha") or "")
        repo_sha = _repo_sha()
        if repo_sha and server_sha:
            assert server_sha.startswith(repo_sha[:12]), {"repo_sha": repo_sha, "server_sha": server_sha}

        status, client_health = _request_json(f"/health?client_build={server_sha}")
        assert status == 200, client_health
        assert (client_health.get("build") or {}).get("client_matches_server") is True, client_health

        status, blocked = _request_json("/health?deep=true")
        assert status == 403, {"status": status, "payload": blocked}

        status, deep = _request_json("/health?deep=true", token=self.token, timeout=25)
        assert status == 200, deep
        assert deep.get("deep") is True, deep
        assert (deep.get("database") or {}).get("database_backend") in {"postgres", "sqlite"}, deep
        assert "pool_health" in (deep.get("database") or {}), deep
        assert "queue" in deep, deep

        status, metrics = _request_json("/api/admin/runtime/metrics", token=self.token, timeout=25)
        assert status == 200, metrics
        assert "requests" in metrics, metrics
        assert "postgres" in metrics, metrics
        assert "ai_providers" in metrics, metrics
        assert (metrics.get("requested_by") or "").endswith("@viltrox.com"), metrics

        return {
            "server_sha": server_sha[:8],
            "repo_sha": repo_sha[:8] if repo_sha else "",
            "request_count": (metrics.get("requests") or {}).get("total_requests"),
            "postgres_ok": (metrics.get("postgres") or {}).get("ok"),
        }


def main() -> int:
    smoke = Smoke()
    try:
        result = smoke.run()
        print("VKPI_P3_15A_MONITORING_SMOKE_OK", json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        smoke.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
