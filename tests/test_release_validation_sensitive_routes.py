"""Release fence + real sensitive routers, without startup, DB or providers.

The extra dependency is an entry witness, not an alternative production fence:
it reports 418 if a mutation ever reaches routing.  Removing the real marker
must reach that witness; with the marker present even dependencies stay idle.
All business/storage/external callbacks below are fail-on-call tripwires.
This proves HTTP admission only, not migration or old-version compatibility.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import main_release_validation
from app.api.routers import auth, dsar_public, vkpi_kol_portal
from app.core import release_validation
from app.db import connection
from app.domains.kol import contact_suppression, dsar_erasure, portal
from app.platform import apify_budget
from app.services.auth import email, token_revocation, tokens
from app.services.security import rate_limiter


_USER = {"id": 17, "email": "release-fixture@example.test"}
_DSAR_BODY = {
    "request_type": "erasure",
    "platform": "youtube",
    "handle": "ReleaseFixture",
    "contact_email": "release-fixture@example.test",
    "consent_confirmed": True,
}
_MUTATIONS = [
    pytest.param("POST", "/api/admin/vkpi/kol-portal/7/issue-token", {}, id="portal-issue"),
    pytest.param("POST", "/api/auth/login", {"email": _USER["email"], "password": "fixture-only"}, id="login-bearer"),
    pytest.param("POST", "/api/auth/login?session=cookie", {"email": _USER["email"], "password": "fixture-only"}, id="login-cookie"),
    pytest.param("POST", "/api/auth/logout", {}, id="logout-revocation"),
    pytest.param("POST", "/api/auth/admin/revoke-sessions/17", {}, id="admin-revocation"),
    pytest.param("POST", "/api/auth/forgot-password", {"email": _USER["email"]}, id="reset-token-issue"),
    pytest.param("POST", "/api/auth/resend-verification", {"email": _USER["email"]}, id="verification-token-issue"),
    pytest.param("POST", "/api/auth/reset-password", {"token": "fixture", "password": "fixture-only"}, id="password-reset-revocation"),
    pytest.param("POST", "/api/auth/change-password", {"current_password": "fixture-old", "new_password": "fixture-new"}, id="password-change-revocation"),
    pytest.param("GET", "/api/auth/verify-email/fixture-token", None, id="get-shaped-email-write"),
    pytest.param("POST", "/api/public/dsar/requests", _DSAR_BODY, id="public-erasure-intake"),
    pytest.param("POST", "/api/public/dsar/requests", {**_DSAR_BODY, "request_type": "do_not_contact"}, id="public-suppression-intake"),
    pytest.param("PATCH", "/api/admin/vkpi/dsar/requests/7", {"status": "approved"}, id="admin-dsar-approve"),
    pytest.param("PATCH", "/api/admin/vkpi/dsar/requests/7", {"status": "rejected"}, id="admin-dsar-reject"),
    pytest.param("POST", "/api/admin/vkpi/dsar/requests/7/execute", {}, id="admin-dsar-execute"),
]
_READ_ONLY_POSTS = (
    "/api/admin/vkpi/intelligent/query",
    "/api/marketing/intelligent/query",
    "/api/admin/vkpi/event-radar/refresh-preview",
)


def _install_tripwires(monkeypatch, calls: list[str]) -> None:
    def forbidden(label):
        def fail(*_args, **_kwargs):
            calls.append(label)
            raise AssertionError(f"sensitive callback reached: {label}")
        return fail

    targets = (
        (connection, ("get_conn", "open_standalone_conn", "db_read", "db_write")),
        (auth, (
            "get_conn", "db_read", "db_write", "get_current_user_async",
            "revoke_user_sessions", "build_login_payload", "apply_auth_cookie",
            "clear_auth_cookie", "send_verification_email", "send_password_reset_email",
            "touch_user_last_login", "import_legacy_user_if_available",
        )),
        (vkpi_kol_portal, ("get_conn",)),
        (portal, ("issue_token",)),
        (token_revocation, ("get_conn", "revoke_user_sessions")),
        (tokens, ("get_conn", "create_email_token")),
        (email, ("create_email_token", "send_email")),
        (dsar_public, (
            "get_conn", "table_exists", "_insert_ticket", "apply_self_suppression",
            "_execute_do_not_contact", "_captcha_gate",
        )),
        (dsar_erasure, ("get_conn", "erase_subject", "_delete_qdrant_points", "_delete_r2_objects")),
        (contact_suppression, ("record_suppression",)),
        (apify_budget, ("call_apify_actor",)),
        (rate_limiter, ("check_rate_limit", "_get_redis")),
    )
    for module, names in targets:
        for name in names:
            monkeypatch.setattr(module, name, forbidden(f"{module.__name__}.{name}"))


@pytest.fixture(params=("verified", "tampered"))
def sensitive_app(request, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    marker = tmp_path / "release-validation.fence"
    marker.write_text(release_validation.FENCE_PAYLOAD, encoding="utf-8")
    marker.chmod(0o444 if request.param == "verified" else 0o600)
    monkeypatch.setattr(release_validation, "IS_PRODUCTION", False)
    monkeypatch.setenv("VKPI_RELEASE_VALIDATION_FENCE_PATH", str(marker))
    assert release_validation.release_validation_status() == {
        "active": True,
        "valid": request.param == "verified",
        "source": "verified_marker" if request.param == "verified" else "invalid_marker",
    }
    callbacks: list[str] = []
    entries: list[tuple[str, str]] = []
    _install_tripwires(monkeypatch, callbacks)

    def current_user(_request):
        callbacks.append("auth.read_current_user_fixture")
        return dict(_USER)

    monkeypatch.setattr(auth, "get_current_user", current_user)

    def entry_witness(incoming: Request):
        entries.append((incoming.method, incoming.url.path))
        if not release_validation.release_validation_request_allowed(
            incoming.method, incoming.url.path, incoming.query_params,
        ):
            raise HTTPException(418, "test-only router entry witness")

    # Real router registrations and real production middleware; no app lifespan
    # or production startup is imported/executed.  No route callable is replaced.
    app = FastAPI(dependencies=[Depends(entry_witness)])
    for router in (auth.router, vkpi_kol_portal.router, dsar_public.router):
        app.include_router(router)

    @app.get("/health")
    def health_dispatch_probe():
        return {"status": "fixture", "release_validation": release_validation.release_validation_status()}

    # These are dispatch probes only; query/provider implementation is covered
    # by its own suite and must never run in this sensitive-route fixture.
    for path in _READ_ONLY_POSTS:
        app.add_api_route(path, lambda: {"read_only_dispatch": True}, methods=["POST"])
    app.add_middleware(main_release_validation.ReleaseValidationFenceMiddleware)
    with TestClient(app) as client:
        yield SimpleNamespace(app=app, client=client, marker=marker, callbacks=callbacks, entries=entries)


def _assert_registered(app: FastAPI, method: str, path: str) -> None:
    assert any(
        isinstance(route, APIRoute)
        and method in route.methods
        and route.path_regex.fullmatch(urlsplit(path).path)
        for route in app.routes
    ), f"test must exercise a registered production route: {method} {path}"


@pytest.mark.parametrize(("method", "path", "body"), _MUTATIONS)
def test_sensitive_mutation_is_fenced_before_routing(sensitive_app, method, path, body):
    _assert_registered(sensitive_app.app, method, path)
    response = sensitive_app.client.request(method, path, json=body)
    assert response.status_code == 503
    assert response.json()["code"] == "release_validation_fenced"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["retry-after"] == "5"
    assert "set-cookie" not in response.headers
    assert sensitive_app.entries == []
    assert sensitive_app.callbacks == []


@pytest.mark.parametrize(("method", "path", "body"), _MUTATIONS)
def test_removing_marker_reaches_only_the_safe_entry_witness(sensitive_app, method, path, body):
    """Negative control: 503 cannot be a missing route or a test-local fence."""
    _assert_registered(sensitive_app.app, method, path)
    sensitive_app.marker.chmod(0o600)
    sensitive_app.marker.unlink()
    response = sensitive_app.client.request(method, path, json=body)
    assert response.status_code == 418
    assert response.json() == {"detail": "test-only router entry witness"}
    assert sensitive_app.entries == [(method, urlsplit(path).path)]
    assert sensitive_app.callbacks == []


def test_reviewed_read_routes_stay_available_without_business_callbacks(sensitive_app):
    client = sensitive_app.client
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json() == {"status": "success", "user": _USER}
    assert "set-cookie" not in response.headers
    policy = client.get("/api/public/legal/policy")
    assert policy.status_code == 200
    assert policy.json()["request_types"] == list(dsar_public.REQUEST_TYPES)
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["release_validation"]["active"] is True
    for path in _READ_ONLY_POSTS:
        response = client.post(path, json={})
        assert response.status_code == 200
        assert response.json() == {"read_only_dispatch": True}
    assert sensitive_app.entries == [
        ("GET", "/api/auth/me"), ("GET", "/api/public/legal/policy"), ("GET", "/health"),
        *(("POST", path) for path in _READ_ONLY_POSTS),
    ]
    assert sensitive_app.callbacks == ["auth.read_current_user_fixture"]


def test_sensitive_write_is_rejected_before_json_parsing(sensitive_app):
    response = sensitive_app.client.post(
        "/api/public/dsar/requests", content="{invalid-json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 503
    assert response.json()["code"] == "release_validation_fenced"
    assert sensitive_app.entries == []
    assert sensitive_app.callbacks == []
