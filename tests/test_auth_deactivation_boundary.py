from __future__ import annotations

import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@pytest.mark.parametrize("status", ["blocked", "suspended"])
def test_disabled_user_cannot_log_in(monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    from app.services.auth import service

    monkeypatch.setattr(service, "IS_PRODUCTION", False)
    monkeypatch.setattr(service, "is_locked_out", lambda _user_id: False)
    monkeypatch.setattr(service, "verify_password", lambda _password, _stored: True)
    monkeypatch.setattr(service, "clear_failed", lambda _user_id: None)

    result = service.validate_login_credentials(
        {"id": 7, "password_hash": "valid", "status": status},
        "correct password",
    )

    assert result == {
        "status": "error",
        "message": "Account is not active — contact support",
    }


def test_inactive_staff_cannot_log_in_or_build_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.auth import service

    inactive_staff = {"id": 42, "active": 0, "organization_scope_status": "staff_inactive"}
    monkeypatch.setattr(service, "IS_PRODUCTION", False)
    monkeypatch.setattr(service, "is_locked_out", lambda _user_id: False)
    monkeypatch.setattr(service, "verify_password", lambda _password, _stored: True)
    monkeypatch.setattr(service, "clear_failed", lambda _user_id: None)
    monkeypatch.setattr(service, "staff_context_for_user", lambda _user: inactive_staff)
    monkeypatch.setattr(service, "make_token", lambda *_args: pytest.fail("inactive staff received a token"))
    user = {"id": 7, "password_hash": "valid", "status": "active"}

    assert service.validate_login_credentials(user, "correct password") == {
        "status": "error",
        "message": "Account is not active — contact support",
    }
    assert service.build_login_payload(user) == {
        "status": "error",
        "message": "Account is not active — contact support",
    }


def test_creator_without_staff_row_can_still_log_in(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.auth import service

    monkeypatch.setattr(service, "IS_PRODUCTION", False)
    monkeypatch.setattr(service, "is_locked_out", lambda _user_id: False)
    monkeypatch.setattr(service, "verify_password", lambda _password, _stored: True)
    monkeypatch.setattr(service, "clear_failed", lambda _user_id: None)
    monkeypatch.setattr(
        service,
        "staff_context_for_user",
        lambda _user: {"organization_scope_status": "staff_missing"},
    )

    assert service.validate_login_credentials(
        {"id": 7, "password_hash": "valid", "status": "approved"},
        "correct password",
    ) is None


@pytest.mark.parametrize("status", ["blocked", "suspended"])
def test_existing_token_is_rejected_for_disabled_user(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    from app.core import permissions, security

    class FakeResult:
        def fetchone(self):
            return {"id": 7, "status": status}

    class FakeConn:
        def execute(self, *_args, **_kwargs):
            return FakeResult()

    monkeypatch.setattr(security, "IS_PRODUCTION", False)
    monkeypatch.setattr(security, "verify_token", lambda _token: {"uid": 7})
    monkeypatch.setattr(security, "cache_get", lambda _key: None)
    monkeypatch.setattr(security, "cache_set", lambda *_args, **_kwargs: pytest.fail("disabled user was cached"))
    monkeypatch.setattr(security, "db_connection_sync_reusing_scope", lambda: nullcontext())
    monkeypatch.setattr(security, "get_conn", lambda: FakeConn())
    monkeypatch.setattr(
        permissions,
        "staff_context_for_user",
        lambda _user: pytest.fail("disabled user reached staff permission resolution"),
    )
    request = SimpleNamespace(
        headers={"Authorization": "Bearer existing-token"},
        cookies={},
        state=SimpleNamespace(),
    )

    assert security.get_current_user(request) is None
    assert not hasattr(request.state, "vkpi_authenticated_user")


def test_existing_token_is_rejected_for_inactive_staff(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import permissions, security

    class FakeResult:
        def fetchone(self):
            return {
                "id": 7,
                "email": "inactive-staff@viltrox.com",
                "status": "active",
                "role": "creator",
            }

    class FakeConn:
        def execute(self, *_args, **_kwargs):
            return FakeResult()

    monkeypatch.setattr(security, "IS_PRODUCTION", False)
    monkeypatch.setattr(security, "verify_token", lambda _token: {"uid": 7})
    monkeypatch.setattr(security, "cache_get", lambda _key: None)
    monkeypatch.setattr(security, "cache_set", lambda *_args, **_kwargs: pytest.fail("inactive staff was cached"))
    monkeypatch.setattr(security, "db_connection_sync_reusing_scope", lambda: nullcontext())
    monkeypatch.setattr(security, "get_conn", lambda: FakeConn())
    monkeypatch.setattr(
        permissions,
        "staff_context_for_user",
        lambda _user: {"id": 42, "active": 0, "organization_scope_status": "staff_inactive"},
    )
    request = SimpleNamespace(
        headers={"Authorization": "Bearer existing-token"},
        cookies={},
        state=SimpleNamespace(),
    )

    assert security.get_current_user(request) is None
    assert not hasattr(request.state, "vkpi_authenticated_user")


def test_inactive_staff_context_and_owner_bypass_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import permissions

    owner_email = next(iter(permissions.OWNER_EMAILS))

    class FakeResult:
        def fetchone(self):
            return {
                "id": 42,
                "user_id": 7,
                "email": owner_email,
                "role": "admin",
                "is_owner": 1,
                "active": 0,
                "permissions_json": '{"vkpi":"admin","system.members":"admin","contacts.reveal":"admin"}',
                "resolved_organization_id": 9,
                "organization_membership_count": 1,
            }

    class FakeConn:
        def execute(self, *_args, **_kwargs):
            return FakeResult()

    monkeypatch.setattr(permissions, "get_conn", lambda: FakeConn())

    context = permissions.staff_context_for_user(
        {"id": 7, "email": owner_email, "role": "admin"}
    )

    assert context["active"] == 0
    assert context["role"] == "readonly"
    assert context["is_owner"] == 0
    assert context["organization_scope_status"] == "staff_inactive"
    assert set(context["permissions"].values()) == {"none"}
    assert permissions.check_tab_permission(context, "vkpi", "read") is False
    assert permissions.check_system_permission(context, "system.members", "read") is False
    assert permissions.check_contact_reveal_permission(context) is False


def test_suspend_and_reactivate_invalidate_auth_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.system import staff

    class FakeResult:
        def fetchone(self):
            return {"user_id": 77}

    class FakeConn:
        commits = 0

        def execute(self, *_args, **_kwargs):
            return FakeResult()

        def commit(self):
            self.commits += 1

    conn = FakeConn()
    invalidated: list[int] = []
    monkeypatch.setattr(staff, "get_conn", lambda: conn)
    monkeypatch.setattr(staff, "invalidate_user_cache", invalidated.append)

    staff.suspend(42, "security review")
    staff.reactivate(42)

    assert conn.commits == 2
    assert invalidated == [77, 77]


def test_staff_role_and_permission_updates_invalidate_auth_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.system import staff

    class FakeResult:
        def fetchone(self):
            return {"user_id": 77, "role": "employee", "is_owner": 0}

    class FakeConn:
        commits = 0

        def execute(self, *_args, **_kwargs):
            return FakeResult()

        def commit(self):
            self.commits += 1

    conn = FakeConn()
    invalidated: list[int] = []
    monkeypatch.setattr(staff, "get_conn", lambda: conn)
    monkeypatch.setattr(staff, "invalidate_user_cache", invalidated.append)

    staff.update(42, {"role": "employee"})
    staff.update_permissions(42, {"vkpi": "none"}, actor_is_owner=True)

    assert conn.commits == 2
    assert invalidated == [77, 77]


def test_user_status_update_invalidates_auth_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.routers import admin_common

    class FakeResult:
        rowcount = 1

    class FakeConn:
        committed = False

        def execute(self, *_args, **_kwargs):
            return FakeResult()

        def commit(self):
            self.committed = True

    conn = FakeConn()
    invalidated: list[int] = []
    monkeypatch.setattr(admin_common, "get_conn", lambda: conn)
    monkeypatch.setattr(admin_common, "invalidate_user_cache", invalidated.append)

    assert admin_common._update_user_status(7, "blocked", "security review") is True
    assert conn.committed is True
    assert invalidated == [7]
