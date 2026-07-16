"""Tenant isolation for legacy-global System Admin surfaces."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.dependencies.auth import get_user_required
from app.api.dependencies.legacy_scope import (
    legacy_system_admin_scope_guard,
    require_legacy_system_admin_scope,
)
from app.core.security import require_admin_async


_DENIED_SCOPES = [
    pytest.param(
        {"organization_id": 4, "organization_scope_status": "resolved"},
        id="resolved-org4-owner",
    ),
    pytest.param(
        {"organization_scope_status": "membership_missing"},
        id="membership-missing-owner",
    ),
    pytest.param(
        {"organization_scope_status": "ambiguous"},
        id="ambiguous-owner",
    ),
]

_PUBLIC_STAFF_ROUTES = {
    ("POST", "/api/admin/staff/accept-invite"),
    ("GET", "/api/admin/staff/invite/status"),
}


def _staff(scope: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": 991001,
        "staff_id": 991001,
        "user_id": 991001,
        "email": "legacy-admin-scope@example.test",
        "role": "admin",
        "is_owner": 1,
        "permissions": {
            "system": "admin",
            "runtime": "admin",
            "command": "admin",
        },
        **scope,
    }


def _make_client(monkeypatch: pytest.MonkeyPatch, staff: dict[str, Any]):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.api.dependencies.legacy_scope as legacy_scope_mod
    import app.api.dependencies.perms as perms_mod
    from app.api.routers import system_admin

    user = {
        "id": staff["user_id"],
        "email": staff["email"],
        "role": "admin",
    }
    admin = {**user, "is_owner": True}
    monkeypatch.setattr(legacy_scope_mod, "staff_context_for_user", lambda _user: staff)
    monkeypatch.setattr(perms_mod, "staff_context_for_user", lambda _user: staff)

    app = FastAPI()
    app.include_router(system_admin.router)
    app.dependency_overrides[get_user_required] = lambda: user
    app.dependency_overrides[require_admin_async] = lambda: admin
    return TestClient(app, raise_server_exceptions=False)


def _bomb(calls: list[str], label: str) -> Callable[..., Any]:
    def fail(*_args: Any, **_kwargs: Any) -> Any:
        calls.append(label)
        raise AssertionError(f"legacy System Admin guard ran too late: {label}")

    return fail


def _block_representative_service_seams(
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    from app.api.routers import system_admin, system_admin_staff

    calls: list[str] = []
    staff_seams = {
        "invite": "staff invite provisioning",
        "create_activation_link": "staff invite activation token",
        "list_members": "staff list",
        "update": "staff update",
        "update_permissions": "staff permission update",
        "suspend": "staff suspend",
        "reactivate": "staff reactivate",
        "resend_invite": "staff invite resend",
        "create_existing_activation_link": "staff activation token",
        "create_password_reset_link": "staff reset token",
        "delete_member": "staff delete",
        "get_audit_log": "staff audit log",
        "list_api_tokens": "API token list",
        "create_api_token": "API token create",
        "revoke_api_token": "API token revoke",
    }
    for attr, label in staff_seams.items():
        monkeypatch.setattr(
            system_admin_staff.staff_svc,
            attr,
            _bomb(calls, label),
        )
    service_seams = (
        (system_admin.int_svc, "list_all", "integration list"),
        (system_admin.int_svc, "health_check_all", "integration health write"),
        (system_admin.int_svc, "smoke_test", "integration smoke test"),
        (system_admin.int_svc, "set_enabled", "integration enable/disable write"),
        (system_admin.rt_svc, "worker_states", "runtime worker read"),
        (system_admin.rt_svc, "run_job_now", "scheduler write"),
        (system_admin.rt_svc, "clear_cache", "cache clear write"),
        (system_admin.provider_svc, "list_provider_status", "provider read"),
        (system_admin.provider_svc, "probe_provider", "provider probe"),
        (system_admin.usage_svc, "usage_summary", "provider usage read"),
        (system_admin.trust_svc, "update_rule", "trust rule write"),
        (system_admin.trust_svc, "list_events", "trust event read"),
        (system_admin.trust_svc, "block_user", "user moderation write"),
    )
    for owner, attr, label in service_seams:
        monkeypatch.setattr(owner, attr, _bomb(calls, label))
    monkeypatch.setattr(
        system_admin,
        "record_admin_action",
        _bomb(calls, "system audit write"),
    )
    monkeypatch.setattr(
        system_admin_staff,
        "record_admin_action",
        _bomb(calls, "staff audit write"),
    )
    return calls


def test_guard_allows_only_explicit_resolved_organization_one() -> None:
    assert legacy_system_admin_scope_guard(
        {"organization_id": 1, "organization_scope_status": "resolved"}
    ) is None
    for staff in (
        {"organization_id": 1, "organization_scope_status": "ambiguous"},
        {"organization_id": 4, "organization_scope_status": "resolved"},
        {"organization_scope_status": "membership_missing"},
        {"organization_scope_status": "ambiguous"},
    ):
        result = legacy_system_admin_scope_guard(staff)
        assert result is not None
        assert result["status"] == "scope_unavailable"
        assert result["writes"] is False


def test_all_legacy_global_system_admin_routes_declare_the_shared_guard() -> None:
    from app.api.routers import system_admin

    observed_exemptions: set[tuple[str, str]] = set()
    for route in system_admin.router.routes:
        methods = set(route.methods or ())
        for method in methods:
            key = (method, route.path)
            dependency_calls = {dep.call for dep in route.dependant.dependencies}
            if key in _PUBLIC_STAFF_ROUTES:
                observed_exemptions.add(key)
                assert require_legacy_system_admin_scope not in dependency_calls
            else:
                assert require_legacy_system_admin_scope in dependency_calls, key
    assert observed_exemptions == _PUBLIC_STAFF_ROUTES


@pytest.mark.parametrize("scope", _DENIED_SCOPES)
def test_non_org1_owner_fails_before_staff_system_or_credential_services(
    monkeypatch: pytest.MonkeyPatch,
    scope: dict[str, Any],
) -> None:
    calls = _block_representative_service_seams(monkeypatch)
    client = _make_client(monkeypatch, _staff(scope))
    requests = [
        ("POST", "/api/admin/staff", {"email": "blocked@example.test"}),
        ("POST", "/api/admin/staff/invite", {"email": "blocked@example.test"}),
        (
            "POST",
            "/api/admin/staff/invite/activation-link",
            {"email": "blocked@example.test"},
        ),
        ("GET", "/api/admin/staff", None),
        ("GET", "/api/admin/staff/invite/capabilities", None),
        ("PATCH", "/api/admin/staff/7", {"role": "manager"}),
        ("POST", "/api/admin/staff/7/permissions", {"permissions": {"vkpi": "read"}}),
        ("POST", "/api/admin/staff/7/suspend", {"reason": "must-not-run"}),
        ("POST", "/api/admin/staff/7/reactivate", None),
        ("POST", "/api/admin/staff/7/resend-invite", None),
        ("POST", "/api/admin/staff/7/activation-link", None),
        ("POST", "/api/admin/staff/7/reset-password-link", None),
        ("DELETE", "/api/admin/staff/7", None),
        ("GET", "/api/admin/staff/roles", None),
        ("GET", "/api/admin/staff/permission-matrix", None),
        ("GET", "/api/admin/staff/audit-log", None),
        ("GET", "/api/admin/staff/api-tokens", None),
        ("POST", "/api/admin/staff/api-tokens", {"name": "must-not-exist"}),
        ("DELETE", "/api/admin/staff/api-tokens/3", None),
        ("GET", "/api/admin/integrations", None),
        ("POST", "/api/admin/integrations/health-check-all", None),
        ("POST", "/api/admin/integrations/2/test", None),
        ("POST", "/api/admin/integrations/2/disable", None),
        ("GET", "/api/admin/runtime/workers", None),
        ("POST", "/api/admin/runtime/scheduler/probe/run-now", None),
        ("POST", "/api/admin/runtime/cache/l1/clear", None),
        ("GET", "/api/admin/system/providers", None),
        ("POST", "/api/admin/system/providers/openai/probe", {}),
        ("GET", "/api/admin/system/usage", None),
        ("GET", "/api/admin/system/models", None),
        ("GET", "/api/admin/trust/events", None),
        ("PUT", "/api/admin/trust/rules/2", {"enabled": False}),
        ("POST", "/api/admin/users/9/block", {"reason": "must-not-run"}),
    ]
    try:
        for method, path, payload in requests:
            response = client.request(method, path, json=payload)
            assert response.status_code == 403, (
                path,
                response.status_code,
                response.text[:500],
            )
            detail = response.json()["detail"]
            assert detail["status"] == "scope_unavailable", (path, detail)
            assert detail["writes"] is False, (path, detail)
            assert detail["organization_scope_status"] == scope["organization_scope_status"]
            assert detail["organization_id"] == scope.get("organization_id")
    finally:
        client.close()
    assert calls == []


def test_resolved_org1_reaches_legacy_staff_and_system_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.routers import system_admin, system_admin_staff

    invite_calls: list[int] = []

    def invite(_body: dict[str, Any], *, inviter_id: int) -> dict[str, Any]:
        invite_calls.append(inviter_id)
        return {"id": 41, "email": "new@example.test", "role": "viewer"}

    monkeypatch.setattr(system_admin_staff.staff_svc, "invite", invite)
    monkeypatch.setattr(system_admin_staff.staff_svc, "list_members", lambda: [{"id": 7}])
    monkeypatch.setattr(system_admin.int_svc, "list_all", lambda: [{"id": 2}])
    monkeypatch.setattr(system_admin_staff, "record_admin_action", lambda **_kwargs: None)
    client = _make_client(
        monkeypatch,
        _staff({"organization_id": 1, "organization_scope_status": "resolved"}),
    )
    try:
        assert client.get("/api/admin/staff").json() == [{"id": 7}]
        assert client.get("/api/admin/integrations").json() == [{"id": 2}]
        invite_response = client.post(
            "/api/admin/staff/invite",
            json={"email": "new@example.test", "role": "viewer"},
        )
        assert invite_response.status_code == 200, invite_response.text
        assert invite_response.json()["id"] == 41
    finally:
        client.close()
    assert invite_calls == [991001]


def test_non_org1_management_invites_fail_closed_but_public_token_flow_remains_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.routers import system_admin_staff

    calls: list[str] = []

    monkeypatch.setattr(
        system_admin_staff.staff_svc,
        "invite",
        _bomb(calls, "staff invite provisioning"),
    )
    monkeypatch.setattr(
        system_admin_staff.staff_svc,
        "create_activation_link",
        _bomb(calls, "staff invite activation token"),
    )
    monkeypatch.setattr(
        system_admin_staff,
        "record_admin_action",
        _bomb(calls, "global admin audit write"),
    )

    def invite_token_status(token: str) -> dict[str, str]:
        return {"status": "valid", "token": token}

    def accept_invite(token: str, password: str) -> dict[str, str]:
        return {"status": "accepted", "token": token, "password": password}

    monkeypatch.setattr(
        system_admin_staff.staff_svc,
        "invite_token_status",
        invite_token_status,
    )
    monkeypatch.setattr(
        system_admin_staff.staff_svc,
        "accept_invite",
        accept_invite,
    )
    client = _make_client(
        monkeypatch,
        _staff({"organization_id": 4, "organization_scope_status": "resolved"}),
    )
    try:
        for path in (
            "/api/admin/staff",
            "/api/admin/staff/invite",
            "/api/admin/staff/invite/activation-link",
        ):
            response = client.post(
                path,
                json={"email": "blocked@example.test", "role": "viewer"},
            )
            assert response.status_code == 403, (path, response.text)
            assert response.json()["detail"]["status"] == "scope_unavailable"
        status_response = client.get("/api/admin/staff/invite/status?token=public-token")
        assert status_response.status_code == 200, status_response.text
        assert status_response.json() == {"status": "valid", "token": "public-token"}
        accept_response = client.post(
            "/api/admin/staff/accept-invite",
            json={"invite_token": "public-token", "password": "safe-password"},
        )
        assert accept_response.status_code == 200, accept_response.text
        assert accept_response.json()["status"] == "accepted"
    finally:
        client.close()
    assert calls == []
