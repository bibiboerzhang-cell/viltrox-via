"""S-08:邀请 / 重置 / 验证 / 门户 token 摘要入库 + 门户 token 过期 + 公开端点限流。"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import uuid

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

_TS = "%Y-%m-%dT%H:%M:%SZ"


@pytest.fixture()
def hermetic_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app.db import connection as db_connection
    from app.db.migrations import init_db

    db_path = (tmp_path / "s08.db").resolve()
    old = (db_connection.DB_PATH, db_connection.DB_RUNTIME_BACKEND, db_connection.DB_RUNTIME_URL)
    db_connection.close_db_runtime_sync()
    db_connection.DB_PATH = db_path
    db_connection.DB_RUNTIME_BACKEND = "sqlite"
    db_connection.DB_RUNTIME_URL = ""
    monkeypatch.setenv("ADMIN_PASSWORD", "vkpi-hermetic-s08-only")
    init_db()
    try:
        yield db_connection.get_conn()
    finally:
        db_connection.close_db_runtime_sync()
        db_connection.DB_PATH, db_connection.DB_RUNTIME_BACKEND, db_connection.DB_RUNTIME_URL = old


def _invite(staff_svc):
    email = f"s08-{uuid.uuid4().hex}@viltrox.com"
    created = staff_svc.create_activation_link(
        {"email": email, "full_name": "S08 Invite", "role": "employee", "permissions": {"vkpi": "read"}},
        inviter_id=1,
    )
    raw = parse_qs(urlparse(str(created["activation_url"])).query)["token"][0]
    return created, raw


# ── email_tokens ──────────────────────────────────────────────────────────────
def test_token_digest_helpers_reject_digest_as_plaintext():
    from app.services.auth.tokens import token_digest, token_lookup_values

    digest = token_digest("abc")
    assert digest.startswith("sha256$") and len(digest) == 7 + 64
    assert token_lookup_values("abc") == (digest, "abc")
    # 提交摘要本身:两个候选都是「摘要的摘要」,绝不会等于库里存的 digest。
    both = token_lookup_values(digest)
    assert both[0] == both[1] == token_digest(digest) != digest


def test_invite_token_stored_as_digest_and_status_has_no_pii(hermetic_db):
    from app.services.auth.tokens import token_digest
    from app.services.system import staff as staff_svc

    created, raw = _invite(staff_svc)
    stored = [
        dict(r)["token"]
        for r in hermetic_db.execute(
            "SELECT token FROM email_tokens WHERE user_id = ? AND type = 'staff_invite'", (created["user_id"],)
        ).fetchall()
    ]
    assert stored == [token_digest(raw)]
    assert raw not in stored
    assert created["expires_at"]  # email_token_expires_at 经摘要回读

    status = staff_svc.invite_token_status(raw)
    assert status["valid"] is True and status["state"] == "active"
    assert "email" not in status and "full_name" not in status

    # 拿库里的摘要当 token 提交 → 不得过闸
    assert staff_svc.invite_token_status(token_digest(raw))["state"] == "invalid"
    with pytest.raises(ValueError, match="invalid invite token"):
        staff_svc.accept_invite(token_digest(raw), "Password123")

    assert staff_svc.accept_invite(raw, "Password123")["ok"] is True
    used = staff_svc.invite_token_status(raw)
    assert used["state"] == "used" and "email" not in used


def test_legacy_plaintext_invite_row_still_accepted(hermetic_db):
    from app.services.system import staff as staff_svc

    created, _ = _invite(staff_svc)
    legacy = f"legacy-plain-{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc)
    hermetic_db.execute(
        "INSERT INTO email_tokens (user_id, token, type, created_at, expires_at) VALUES (?,?,?,?,?)",
        (created["user_id"], legacy, "staff_invite", now.strftime(_TS), (now + timedelta(hours=1)).strftime(_TS)),
    )
    hermetic_db.commit()
    assert staff_svc.invite_token_status(legacy)["state"] == "active"
    assert staff_svc.accept_invite(legacy, "Password123")["ok"] is True


def test_password_reset_link_stores_digest(hermetic_db):
    from app.services.auth.tokens import token_digest
    from app.services.system import staff as staff_svc

    created, _ = _invite(staff_svc)
    result = staff_svc.create_password_reset_link(int(created["staff_id"]))
    raw = parse_qs(urlparse(str(result["reset_url"])).query)["reset_token"][0]
    row = hermetic_db.execute(
        "SELECT token FROM email_tokens WHERE user_id = ? AND type = 'reset_password'", (created["user_id"],)
    ).fetchone()
    assert dict(row)["token"] == token_digest(raw)
    assert result["expires_at"] and result["token_hint"] != raw


def test_auth_router_reset_lookup_uses_digest(hermetic_db):
    from app.api.routers import auth as auth_router
    from app.services.auth.tokens import create_email_token, token_digest
    from app.services.system import staff as staff_svc

    created, _ = _invite(staff_svc)
    raw = create_email_token(int(created["user_id"]), "reset_password")
    assert auth_router._fetch_email_token(raw, "reset_password") is not None
    assert auth_router._fetch_email_token(token_digest(raw), "reset_password") is None


# ── portal tokens ─────────────────────────────────────────────────────────────
def _portal_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE vkpi_kol_portal_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_pool_id INTEGER NOT NULL,
            token TEXT NOT NULL UNIQUE,
            revoked BOOLEAN NOT NULL DEFAULT FALSE,
            created_by_staff_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            expires_at TEXT
        )
        """
    )
    return conn


def _live_rows(conn, pool_id):
    return [dict(r) for r in conn.execute(
        "SELECT token, expires_at FROM vkpi_kol_portal_tokens WHERE kol_pool_id=? AND revoked = FALSE", (pool_id,)
    ).fetchall()]


def test_portal_token_hashed_expiring_and_rotating(monkeypatch):
    from app.domains.kol import portal

    monkeypatch.delenv("VKPI_PORTAL_TOKEN_TTL_DAYS", raising=False)
    conn = _portal_conn()
    raw = portal.issue_token(conn, 7, created_by_staff_id=3)
    live = _live_rows(conn, 7)
    assert len(live) == 1 and live[0]["token"] == portal.token_digest(raw) != raw
    expires = datetime.strptime(live[0]["expires_at"], _TS).replace(tzinfo=timezone.utc)
    assert timedelta(days=89) < expires - datetime.now(timezone.utc) <= timedelta(days=90)

    assert portal.resolve_token(conn, raw) == 7
    assert portal.resolve_token(conn, portal.token_digest(raw)) is None
    assert portal.resolve_token(conn, "") is None

    rotated = portal.issue_token(conn, 7)
    assert rotated != raw
    assert portal.resolve_token(conn, raw) is None
    assert portal.resolve_token(conn, rotated) == 7
    assert len(_live_rows(conn, 7)) == 1


def test_portal_token_expiry_and_legacy_rows():
    from app.domains.kol import portal

    conn = _portal_conn()
    raw = portal.issue_token(conn, 9)
    conn.execute(
        "UPDATE vkpi_kol_portal_tokens SET expires_at=? WHERE kol_pool_id=9",
        ((datetime.now(timezone.utc) - timedelta(seconds=1)).strftime(_TS),),
    )
    assert portal.resolve_token(conn, raw) is None

    old = (datetime.now(timezone.utc) - timedelta(days=100)).strftime(_TS)
    fresh = (datetime.now(timezone.utc) - timedelta(days=10)).strftime(_TS)
    conn.execute(
        "INSERT INTO vkpi_kol_portal_tokens (kol_pool_id, token, created_at, expires_at) VALUES (11, 'legacy-old', ?, NULL)",
        (old,),
    )
    conn.execute(
        "INSERT INTO vkpi_kol_portal_tokens (kol_pool_id, token, created_at, expires_at) VALUES (12, 'legacy-fresh', ?, NULL)",
        (fresh,),
    )
    assert portal.resolve_token(conn, "legacy-old") is None  # NULL expires_at 按 created_at + 90d 判
    assert portal.resolve_token(conn, "legacy-fresh") == 12


def test_portal_token_ttl_env(monkeypatch):
    from app.domains.kol import portal

    monkeypatch.setenv("VKPI_PORTAL_TOKEN_TTL_DAYS", "30")
    assert portal.portal_token_ttl_days() == 30
    monkeypatch.setenv("VKPI_PORTAL_TOKEN_TTL_DAYS", "nope")
    assert portal.portal_token_ttl_days() == 90
    monkeypatch.setenv("VKPI_PORTAL_TOKEN_TTL_DAYS", "-5")
    assert portal.portal_token_ttl_days() == 90


# ── public staff endpoints are rate limited ───────────────────────────────────
def _route(router, path: str):
    for route in router.routes:
        if getattr(route, "path", "") == path:
            return route
    raise AssertionError(f"route missing: {path}")


def _fake_request():
    from fastapi import Request

    return Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b"", "client": None})


def test_public_staff_endpoints_are_rate_limited(monkeypatch):
    from fastapi import HTTPException

    from app.api.routers import system_admin_staff
    from app.services.security import rate_limiter

    seen: list[str] = []

    def _deny(bucket, client_id, max_requests, window_sec):
        seen.append(bucket)
        return False, 0

    monkeypatch.setattr(rate_limiter, "check_rate_limit", _deny)
    monkeypatch.setattr(rate_limiter, "_resolve_user", lambda request: None)

    accept = _route(system_admin_staff.public_staff_router, "/staff/accept-invite").endpoint
    status = _route(system_admin_staff.public_staff_router, "/staff/invite/status").endpoint
    for endpoint, kwargs in ((accept, {"body": {}}), (status, {"token": "x"})):
        with pytest.raises(HTTPException) as exc:
            result = endpoint(_fake_request(), **kwargs)
            if asyncio.iscoroutine(result):
                asyncio.run(result)
        assert exc.value.status_code == 429
    assert seen == ["login_register", "login_register"]
