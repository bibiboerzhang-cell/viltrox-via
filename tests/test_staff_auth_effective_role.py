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
    monkeypatch.setattr(security, "db_connection_sync_scope", lambda: nullcontext())
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


def test_staff_invite_token_is_single_use_and_does_not_promote_user_role():
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
