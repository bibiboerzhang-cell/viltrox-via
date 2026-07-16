from __future__ import annotations

import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
import uuid

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@pytest.fixture()
def hermetic_staff_auth_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Run the invite lifecycle against a private initialized SQLite database."""
    from app.db import connection as db_connection
    from app.db.migrations import init_db

    db_path = (tmp_path / "staff-auth.db").resolve()
    assert db_path != (Path(__file__).resolve().parents[1] / "submissions.db").resolve()
    old_db_path = db_connection.DB_PATH
    old_runtime_backend = db_connection.DB_RUNTIME_BACKEND
    old_runtime_url = db_connection.DB_RUNTIME_URL
    db_connection.close_db_runtime_sync()
    db_connection.DB_PATH = db_path
    db_connection.DB_RUNTIME_BACKEND = "sqlite"
    db_connection.DB_RUNTIME_URL = ""
    monkeypatch.setenv("ADMIN_PASSWORD", "vkpi-hermetic-staff-auth-only")
    init_db()
    conn = db_connection.get_conn()
    actual_path = Path(str(conn.execute("PRAGMA database_list").fetchone()[2])).resolve()
    assert actual_path == db_path
    try:
        yield db_path
    finally:
        db_connection.close_db_runtime_sync()
        db_connection.DB_PATH = old_db_path
        db_connection.DB_RUNTIME_BACKEND = old_runtime_backend
        db_connection.DB_RUNTIME_URL = old_runtime_url


def test_login_payload_uses_staff_role_over_legacy_admin(monkeypatch):
    from app.services.auth import service

    monkeypatch.setattr(
        service,
        "staff_context_for_user",
        lambda user: {
            "id": 42,
            "role": "employee",
            "permissions": {"vkpi": "write"},
            "is_owner": 0,
        },
    )

    payload = service.build_login_payload(
        {
            "id": 7,
            "email": "mandy@viltrox.com",
            "name": "Mandy",
            "creator_code": "mandy",
            "role": "admin",
            "points_balance": 0,
            "points_pending": 0,
            "points_total": 0,
            "avatar_url": "",
            "bio": "",
            "signature": "",
            "tier_status": "pending",
            "trust_score": 30,
            "trust_updated_at": "",
        }
    )

    assert payload["user"]["role"] == "employee"
    assert payload["user"]["staff_role"] == "employee"
    assert payload["user"]["auth_role"] == "admin"
    assert payload["user"]["permissions"]["vkpi"] == "write"


def test_current_user_legacy_admin_staff_employee_is_not_admin(monkeypatch):
    from fastapi import HTTPException

    from app.core import permissions, security

    class FakeResult:
        def fetchone(self):
            return {
                "id": 7,
                "email": "mandy@viltrox.com",
                "name": "Mandy",
                "creator_code": "mandy",
                "status": "active",
                "role": "admin",
                "points_balance": 0,
                "points_pending": 0,
                "points_total": 0,
                "avatar_url": "",
                "bio": "",
                "signature": "",
                "tier_status": "pending",
                "trust_score": 30,
                "trust_updated_at": "",
            }

    class FakeConn:
        def execute(self, *_args, **_kwargs):
            return FakeResult()

    monkeypatch.setattr(security, "verify_token", lambda token: {"uid": 7})
    monkeypatch.setattr(security, "cache_get", lambda key: None)
    monkeypatch.setattr(security, "cache_set", lambda *args, **kwargs: None)
    monkeypatch.setattr(security, "db_connection_sync_reusing_scope", lambda: nullcontext())
    monkeypatch.setattr(security, "get_conn", lambda: FakeConn())
    monkeypatch.setattr(
        permissions,
        "staff_context_for_user",
        lambda user: {
            "id": 42,
            "role": "employee",
            "permissions": {"vkpi": "write"},
            "is_owner": 0,
        },
    )

    request = SimpleNamespace(headers={"Authorization": "Bearer token"}, cookies={})
    user = security.get_current_user(request)
    assert user["role"] == "employee"
    assert user["auth_role"] == "admin"
    assert user["staff_id"] == 42

    with pytest.raises(HTTPException) as exc:
        security.require_admin(request)
    assert exc.value.status_code == 403


def test_current_user_loads_staff_before_bounded_db_scope_closes(monkeypatch):
    """Cold-token auth must not retain an outer request connection for staff."""
    from app.core import permissions, security

    events: list[str] = []

    class Scope:
        def __enter__(self):
            events.append("enter")

        def __exit__(self, *_args):
            events.append("exit")

    class FakeResult:
        def fetchone(self):
            return {
                "id": 7,
                "email": "bounded-scope@viltrox.com",
                "name": "Bounded Scope",
                "creator_code": "bounded",
                "status": "approved",
                "role": "admin",
                "points_balance": 0,
                "points_pending": 0,
                "points_total": 0,
                "avatar_url": "",
                "bio": "",
                "signature": "",
                "tier_status": "pending",
                "trust_score": 30,
                "trust_updated_at": "",
            }

    class FakeConn:
        def execute(self, *_args, **_kwargs):
            events.append("user-query")
            return FakeResult()

    def staff_context(_user):
        assert events == ["enter", "user-query"]
        events.append("staff-query")
        return {
            "id": 42,
            "role": "admin",
            "permissions": {"vkpi": "admin"},
            "is_owner": 1,
        }

    monkeypatch.setattr(security, "cache_get", lambda _key: None)
    monkeypatch.setattr(security, "cache_set", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(security, "db_connection_sync_reusing_scope", lambda: Scope())
    monkeypatch.setattr(security, "get_conn", lambda: FakeConn())
    monkeypatch.setattr(permissions, "staff_context_for_user", staff_context)

    user = security._load_user_for_auth(7, "auth:test")

    assert user["staff_id"] == 42
    assert events == ["enter", "user-query", "staff-query", "exit"]


def test_current_user_reuses_verified_principal_within_one_request(monkeypatch):
    """Admin middleware plus route dependency must perform one auth load."""
    from app.core import security

    calls: list[tuple[int, str]] = []
    loaded = {"id": 7, "role": "admin", "staff_id": 42}
    request = SimpleNamespace(
        headers={"Authorization": "Bearer request-token"},
        cookies={},
        state=SimpleNamespace(),
    )

    monkeypatch.setattr(security, "verify_token", lambda _token: {"uid": 7})

    def load_user(user_id: int, cache_key: str):
        calls.append((user_id, cache_key))
        return loaded

    monkeypatch.setattr(security, "_load_user_for_auth", load_user)

    assert security.get_current_user(request) is loaded
    assert security.get_current_user(request) is loaded
    assert len(calls) == 1


def test_staff_invite_token_is_single_use_and_does_not_promote_user_role(hermetic_staff_auth_db):
    from app.db.connection import get_conn
    from app.services.auth.tokens import create_email_token
    from app.services.system import staff as staff_svc

    conn = get_conn()
    email = f"mandy-{uuid.uuid4().hex}@viltrox.com"
    user_id = 0
    staff_id = 0
    try:
        created = staff_svc.create_activation_link(
            {
                "email": email,
                "full_name": "Mandy Invite Test",
                "role": "employee",
                "permissions": {"vkpi": "write"},
            },
            inviter_id=1,
        )
        user_id = int(created["user_id"])
        staff_id = int(created["staff_id"])
        token = parse_qs(urlparse(str(created["activation_url"])).query)["token"][0]

        active = staff_svc.invite_token_status(token)
        assert active["valid"] is True
        assert active["state"] == "active"
        extra_token = create_email_token(user_id, "staff_invite")

        accepted = staff_svc.accept_invite(token, "Password123")
        assert accepted["ok"] is True
        used = staff_svc.invite_token_status(token)
        assert used["valid"] is False
        assert used["state"] == "used"
        extra_used = staff_svc.invite_token_status(extra_token)
        assert extra_used["valid"] is False
        assert extra_used["state"] == "used"

        with pytest.raises(ValueError, match="already used"):
            staff_svc.accept_invite(token, "Password456")
        with pytest.raises(ValueError, match="already activated"):
            staff_svc.create_existing_activation_link(staff_id, inviter_id=1)

        user = conn.execute("SELECT role, email_verified, status FROM users WHERE id=?", (user_id,)).fetchone()
        assert dict(user)["role"] == "creator"
        assert int(dict(user)["email_verified"]) == 1
        assert dict(user)["status"] == "active"
    finally:
        if user_id:
            conn.execute("DELETE FROM email_tokens WHERE user_id=?", (user_id,))
        if staff_id:
            conn.execute("DELETE FROM staff WHERE id=?", (staff_id,))
        if user_id:
            conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()
