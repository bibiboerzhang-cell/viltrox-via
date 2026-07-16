from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.requests import Request


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.dependencies.auth import get_user_required  # noqa: E402
from app.core import security  # noqa: E402
from app.main import _admin_rbac_allowed, app  # noqa: E402
from app.services.auth import sse_tickets  # noqa: E402


_USER = {
    "id": 41,
    "staff_id": 9,
    "role": "admin",
    "is_owner": True,
    "permissions": {"vkpi": "admin"},
}
_ACTIVITY_PATH = "/api/admin/vkpi/activity/stream"
_PROGRESS_PATH = "/api/admin/vkpi/progress/center/stream"


def _request(path: str, *, cookie_name: str = "", ticket: str = "", query: bytes = b"") -> Request:
    headers = []
    if cookie_name and ticket:
        headers.append((b"cookie", f"{cookie_name}={ticket}".encode("ascii")))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 123),
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": query,
            "headers": headers,
            "app": app,
        }
    )


def setup_function() -> None:
    app.dependency_overrides.clear()
    sse_tickets._reset_ticket_store_for_tests()


def teardown_function() -> None:
    app.dependency_overrides.clear()
    sse_tickets._reset_ticket_store_for_tests()


def test_ticket_endpoint_returns_no_secret_and_sets_scoped_httponly_cookie(monkeypatch) -> None:
    monkeypatch.setattr(sse_tickets, "_get_redis", lambda: None)
    app.dependency_overrides[get_user_required] = lambda: _USER
    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/auth/sse-ticket",
        json={"endpoint": _ACTIVITY_PATH},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "expires_in": sse_tickets.ticket_ttl_seconds()}
    assert "ticket" not in response.text.lower()
    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert f"Path={_ACTIVITY_PATH}" in set_cookie
    assert "access_token" not in set_cookie
    assert response.headers["cache-control"] == "no-store"


def test_ticket_is_path_bound_and_consumed_once(monkeypatch) -> None:
    monkeypatch.setattr(sse_tickets, "_get_redis", lambda: None)
    monkeypatch.setattr(security, "_load_user_for_auth", lambda user_id, cache_key: {**_USER, "id": user_id})
    ticket = sse_tickets.issue_sse_ticket(user_id=_USER["id"], endpoint=_ACTIVITY_PATH)
    cookie_name = sse_tickets.ticket_cookie_name(_ACTIVITY_PATH)

    wrong_request = _request(_PROGRESS_PATH, cookie_name=cookie_name, ticket=ticket)
    assert security.get_current_user_stream(wrong_request) is None

    first = _request(_ACTIVITY_PATH, cookie_name=cookie_name, ticket=ticket)
    assert security.get_current_user_stream(first)["id"] == _USER["id"]
    replay = _request(_ACTIVITY_PATH, cookie_name=cookie_name, ticket=ticket)
    assert security.get_current_user_stream(replay) is None


def test_legacy_query_jwt_is_rejected_without_consuming_valid_ticket(monkeypatch) -> None:
    monkeypatch.setattr(sse_tickets, "_get_redis", lambda: None)
    monkeypatch.setattr(security, "_load_user_for_auth", lambda user_id, cache_key: {**_USER, "id": user_id})
    ticket = sse_tickets.issue_sse_ticket(user_id=_USER["id"], endpoint=_ACTIVITY_PATH)
    cookie_name = sse_tickets.ticket_cookie_name(_ACTIVITY_PATH)

    legacy = _request(
        _ACTIVITY_PATH,
        cookie_name=cookie_name,
        ticket=ticket,
        query=b"access_token=header.payload.signature",
    )
    assert security.get_current_user_stream(legacy) is None
    assert "access_token" not in legacy.url.query
    assert "header.payload.signature" not in legacy.url.query

    clean = _request(_ACTIVITY_PATH, cookie_name=cookie_name, ticket=ticket)
    assert security.get_current_user_stream(clean)["id"] == _USER["id"]


def test_ticket_issuer_rejects_unapproved_stream_path(monkeypatch) -> None:
    monkeypatch.setattr(sse_tickets, "_get_redis", lambda: None)
    app.dependency_overrides[get_user_required] = lambda: _USER
    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/auth/sse-ticket",
        json={"endpoint": "/api/admin/users/stream"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert response.status_code == 400
    assert "ticket" not in response.text.lower()


def test_legacy_query_jwt_is_rejected_by_the_mounted_stream_route() -> None:
    legacy = "header.payload.signature"
    response = TestClient(app, raise_server_exceptions=False).get(
        f"{_ACTIVITY_PATH}?access_token={legacy}",
    )
    assert response.status_code in {401, 403}
    assert legacy not in response.text


def test_encoded_task_endpoint_has_one_canonical_cookie_scope() -> None:
    encoded = "/api/audit/stream/a%20b"
    decoded = "/api/audit/stream/a b"
    assert sse_tickets.normalize_sse_endpoint(encoded) == encoded
    assert sse_tickets.normalize_sse_endpoint(decoded) == encoded
    assert sse_tickets.ticket_cookie_name(encoded) == sse_tickets.ticket_cookie_name(decoded)


def test_task_stream_owner_check_is_fail_closed() -> None:
    from app.api.routers.sse import _can_view_task, _safe_task_payload

    snapshot = {"user_id": 7, "triggered_by_staff_id": 11}
    assert _can_view_task({"id": 7, "staff_id": 2, "role": "employee"}, snapshot) is True
    assert _can_view_task({"id": 8, "staff_id": 11, "role": "employee"}, snapshot) is True
    assert _can_view_task({"id": 8, "staff_id": 12, "role": "employee"}, snapshot) is False
    assert _can_view_task({"id": 8, "role": "admin"}, snapshot) is True
    projected = _safe_task_payload(
        {"task_id": "t-1", "status": "done", "access_token": "secret", "payload_json": "private"}
    )
    assert projected == {"task_id": "t-1", "status": "done"}


def test_post_stream_response_uses_normal_bearer_auth_not_eventsource_ticket(monkeypatch) -> None:
    """POST streaming responses are not EventSource subscriptions.

    The advisor staged stream carries a normal bearer token and is protected by
    the route's write dependency.  Only GET ``*/stream`` subscriptions may
    consume the one-time SSE cookie; otherwise the middleware rejects the POST
    before the endpoint can emit its accepted/final contract.
    """

    path = "/api/admin/vkpi/marketing-advisor/threads/thread-1/messages/stream"
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 123),
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": [(b"authorization", b"Bearer test-token")],
            "app": app,
        }
    )
    normal_user = {**_USER, "permissions": {"vkpi": "admin"}}
    monkeypatch.setattr("app.main.get_current_user", lambda _request: normal_user)
    monkeypatch.setattr(
        "app.core.security.get_current_user_stream",
        lambda _request: (_ for _ in ()).throw(AssertionError("POST must not consume an SSE ticket")),
    )
    monkeypatch.setattr("app.main.staff_context_for_user", lambda user: user)
    monkeypatch.setattr("app.main.check_tab_permission", lambda *_args, **_kwargs: True)

    assert _admin_rbac_allowed(request) is True
