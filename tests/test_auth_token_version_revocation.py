"""S-02:登录令牌的服务端吊销(users.token_version)+ cookie 会话绝不回 JWT。

覆盖:
* JWT 载荷带 ``tv``;缺 ``tv`` 的旧令牌按 0 比对(上线当刻不踢人)。
* 版本号不等 → get_current_user 拒绝(端点侧 401 / ``status=error``)。
* 登出 / 改密 / 重置密码 / 管理员踢人 各自 +1;旧 cookie 立即失效(同进程缓存被清)。
* ``Authorization: Bearer cookie-session`` 占位头等价于「走 cookie」。
* cookie 会话客户端的 login(``?session=cookie``)/ 改名 / 改密 响应体里没有 token 字段;
  真 Bearer(脚本)客户端照旧拿到。
* 本地 SQLite 运行时缺列自愈(迁移 307 只在 Postgres 跑)。

全部密闭:内存 SQLite 冒充 users 表,staff 上下文打桩,不碰任何真库。
"""
from __future__ import annotations

import sqlite3
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routers import auth as auth_router  # noqa: E402
from app.core import permissions, security  # noqa: E402
from app.main import app  # noqa: E402
from app.services.auth import service as auth_service  # noqa: E402
from app.services.auth import token_revocation  # noqa: E402
from app.services.cache import cache_clear  # noqa: E402

STAFF_UID = 7
ADMIN_UID = 9
XHR = {"X-Requested-With": "XMLHttpRequest"}


def _memory_users_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # 故意不建 token_version 列:本地 SQLite 运行时靠 token_revocation 自愈加列。
    conn.execute(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY, email TEXT, name TEXT, creator_code TEXT,
            status TEXT, role TEXT, password_hash TEXT,
            points_balance INTEGER DEFAULT 0, points_pending INTEGER DEFAULT 0, points_total INTEGER DEFAULT 0,
            avatar_url TEXT, bio TEXT, signature TEXT, tier_status TEXT, trust_score REAL, trust_updated_at TEXT,
            email_verified INTEGER DEFAULT 1, last_seen_at TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO users (id,email,name,creator_code,status,role,password_hash) VALUES (?,?,?,?,?,?,?)",
        [
            (STAFF_UID, "staff@example.test", "Staff", "VX7", "active", "creator", "hash-7"),
            (ADMIN_UID, "admin@example.test", "Admin", "VX9", "active", "admin", "hash-9"),
        ],
    )
    conn.commit()
    return conn


def _staff_context(user: dict) -> dict:
    return {"id": 100 + int(user.get("id") or 0), "role": str(user.get("role") or "creator"), "permissions": {}, "is_owner": False}


@pytest.fixture
def stack(monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    conn = _memory_users_db()
    token_revocation._reset_for_tests()
    cache_clear(prefix=token_revocation.AUTH_USER_CACHE_PREFIX)
    monkeypatch.setattr(security, "IS_PRODUCTION", False)
    monkeypatch.setattr(security, "get_conn", lambda: conn)
    monkeypatch.setattr(security, "db_connection_sync_reusing_scope", lambda: nullcontext())
    monkeypatch.setattr(token_revocation, "get_conn", lambda: conn)
    monkeypatch.setattr(token_revocation, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(auth_router, "get_conn", lambda: conn)
    monkeypatch.setattr(auth_service, "IS_PRODUCTION", False)
    monkeypatch.setattr(permissions, "staff_context_for_user", _staff_context)
    monkeypatch.setattr(auth_service, "staff_context_for_user", _staff_context)
    yield conn
    token_revocation._reset_for_tests()
    cache_clear(prefix=token_revocation.AUTH_USER_CACHE_PREFIX)
    conn.close()


def _request(*, bearer: str = "", cookie: str = "", query: dict | None = None) -> SimpleNamespace:
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    cookies = {security.AUTH_COOKIE_NAME: cookie} if cookie else {}
    return SimpleNamespace(headers=headers, cookies=cookies, state=SimpleNamespace(), query_params=query or {})


def _cookie_header(token: str) -> dict:
    return {"Cookie": f"{security.AUTH_COOKIE_NAME}={token}", **XHR}


def _me(client: TestClient, token: str) -> dict:
    response = client.get("/api/auth/me", headers=_cookie_header(token))
    assert response.status_code == 200
    return response.json()


def _stored_version(conn: sqlite3.Connection, uid: int) -> int:
    return int(conn.execute("SELECT COALESCE(token_version, 0) FROM users WHERE id=?", (uid,)).fetchone()[0])


# ── 载荷与比对口径 ────────────────────────────────────────────────────────────


def test_make_token_carries_token_version_and_legacy_payload_counts_as_zero() -> None:
    payload = security.verify_token(security.make_token(42, "creator", 3))
    assert payload is not None
    assert payload[token_revocation.TOKEN_VERSION_CLAIM] == 3
    assert security.verify_token(security.make_token(42, "creator"))[token_revocation.TOKEN_VERSION_CLAIM] == 0
    # 旧令牌没有 tv:等价 0,与「从未吊销」(NULL / 0)匹配,与 bump 过的不匹配。
    assert token_revocation.token_version_matches({"uid": 42}, None) is True
    assert token_revocation.token_version_matches({"uid": 42}, 0) is True
    assert token_revocation.token_version_matches({"uid": 42}, 1) is False
    assert token_revocation.token_version_matches({"tv": 2}, 2) is True
    assert token_revocation.token_version_matches({"tv": "garbage"}, 0) is True
    assert token_revocation.coerce_token_version(-5) == 0


def test_get_current_user_rejects_token_version_mismatch_and_sqlite_self_heals(stack: sqlite3.Connection) -> None:
    old_token = security.make_token(STAFF_UID, "creator", 0)
    user = security.get_current_user(_request(bearer=old_token))
    assert user is not None and user["id"] == STAFF_UID
    assert user["token_version"] == 0

    assert token_revocation.revoke_user_sessions(STAFF_UID, reason="test") == 1
    columns = {row[1] for row in stack.execute("PRAGMA table_info(users)").fetchall()}
    assert "token_version" in columns  # 缺列自愈
    assert _stored_version(stack, STAFF_UID) == 1

    assert security.get_current_user(_request(bearer=old_token)) is None
    fresh = security.make_token(STAFF_UID, "creator", 1)
    assert security.get_current_user(_request(bearer=fresh))["token_version"] == 1
    # 其它用户不受影响。
    assert security.get_current_user(_request(bearer=security.make_token(ADMIN_UID, "admin", 0)))["id"] == ADMIN_UID


def test_cookie_session_marker_bearer_means_use_the_cookie(stack: sqlite3.Connection) -> None:
    token = security.make_token(STAFF_UID, "creator", 0)
    marker = security.COOKIE_SESSION_MARKER
    assert security.get_current_user(_request(bearer=marker, cookie=token))["id"] == STAFF_UID
    assert security.get_current_user(_request(bearer=marker)) is None
    assert security.request_uses_cookie_session(_request(bearer=marker, cookie=token)) is True
    assert security.request_uses_cookie_session(_request(cookie=token)) is True
    assert security.request_uses_cookie_session(_request(bearer=token, cookie=token)) is False
    assert security.request_uses_cookie_session(_request(query={"session": "cookie"})) is True


def test_login_payload_signs_with_the_users_current_version(stack: sqlite3.Connection) -> None:
    token_revocation.revoke_user_sessions(STAFF_UID)
    token_revocation.revoke_user_sessions(STAFF_UID)
    row = stack.execute("SELECT * FROM users WHERE id=?", (STAFF_UID,)).fetchone()
    payload = auth_service.build_login_payload(row)
    assert payload["status"] == "success"
    assert security.verify_token(payload["token"])[token_revocation.TOKEN_VERSION_CLAIM] == 2
    assert security.get_current_user(_request(bearer=payload["token"]))["id"] == STAFF_UID


# ── 端点:登出 / 改密 / 重置 / 踢人 ───────────────────────────────────────────


def test_logout_revokes_the_old_cookie_server_side(stack: sqlite3.Connection) -> None:
    client = TestClient(app, raise_server_exceptions=False)
    token = security.make_token(STAFF_UID, "creator", 0)
    assert _me(client, token)["status"] == "success"

    response = client.post("/api/auth/logout", headers=_cookie_header(token))
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert security.AUTH_COOKIE_NAME in response.headers.get("set-cookie", "")
    assert _stored_version(stack, STAFF_UID) == 1

    # 旧令牌签名仍有效,但版本号已前进:被拒。
    assert _me(client, token)["status"] == "error"
    assert security.get_current_user(_request(bearer=token)) is None
    # 再登出一次(已失效令牌)仍幂等成功,且不再 bump。
    again = client.post("/api/auth/logout", headers=_cookie_header(token))
    assert again.status_code == 200 and again.json()["status"] == "success"
    assert _stored_version(stack, STAFF_UID) == 1


def test_change_password_revokes_other_sessions_and_reissues_this_one(
    stack: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth_router, "verify_password", lambda _pw, _stored: True)
    monkeypatch.setattr(auth_router, "hash_password", lambda _pw: "new-hash")
    client = TestClient(app, raise_server_exceptions=False)
    old_token = security.make_token(STAFF_UID, "creator", 0)
    other_device = security.make_token(STAFF_UID, "creator", 0)

    response = client.post(
        "/api/auth/change-password",
        json={"current_password": "old-pw-123", "new_password": "new-pw-12345"},
        headers=_cookie_header(old_token),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert "token" not in body  # cookie 会话客户端绝不在响应体里拿到 JWT
    assert body.get("session") == "cookie"
    assert _stored_version(stack, STAFF_UID) == 1
    assert stack.execute("SELECT password_hash FROM users WHERE id=?", (STAFF_UID,)).fetchone()[0] == "new-hash"

    reissued = response.cookies.get(security.AUTH_COOKIE_NAME)
    assert reissued and reissued != old_token
    assert security.verify_token(reissued)[token_revocation.TOKEN_VERSION_CLAIM] == 1
    assert _me(client, reissued)["status"] == "success"  # 本端续期
    assert _me(client, other_device)["status"] == "error"  # 其它端下线
    assert "new-hash" not in response.text


def test_reset_password_revokes_all_sessions(stack: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        auth_router,
        "_fetch_email_token",
        lambda _token, _type: {"id": 1, "user_id": STAFF_UID, "expires_at": "2999-01-01T00:00:00Z", "used_at": None},
    )
    monkeypatch.setattr(auth_router, "_reset_password_sync", lambda *_args: None)
    monkeypatch.setattr(auth_router, "hash_password", lambda _pw: "reset-hash")
    client = TestClient(app, raise_server_exceptions=False)
    leaked = security.make_token(STAFF_UID, "creator", 0)
    assert _me(client, leaked)["status"] == "success"

    response = client.post("/api/auth/reset-password", json={"token": "reset-t", "password": "new-pw-12345"}, headers=XHR)
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert _stored_version(stack, STAFF_UID) == 1
    assert _me(client, leaked)["status"] == "error"


def test_admin_can_revoke_another_users_sessions_and_non_admin_cannot(stack: sqlite3.Connection) -> None:
    client = TestClient(app, raise_server_exceptions=False)
    victim = security.make_token(STAFF_UID, "creator", 0)
    admin = security.make_token(ADMIN_UID, "admin", 0)
    assert _me(client, victim)["status"] == "success"

    forbidden = client.post(f"/api/auth/admin/revoke-sessions/{ADMIN_UID}", headers=_cookie_header(victim))
    assert forbidden.status_code == 403
    assert _stored_version(stack, ADMIN_UID) == 0

    missing = client.post("/api/auth/admin/revoke-sessions/424242", headers=_cookie_header(admin))
    assert missing.status_code == 404

    kicked = client.post(f"/api/auth/admin/revoke-sessions/{STAFF_UID}", headers=_cookie_header(admin))
    assert kicked.status_code == 200
    assert kicked.json() == {"status": "success", "user_id": STAFF_UID, "token_version": 1}
    assert _me(client, victim)["status"] == "error"
    assert _me(client, admin)["status"] == "success"  # 管理员自己的会话不受影响


# ── 响应体里不回 JWT ──────────────────────────────────────────────────────────


def _stub_login(monkeypatch: pytest.MonkeyPatch, token: str) -> None:
    user = {"id": STAFF_UID, "email": "staff@example.test", "password_hash": "h", "status": "active", "role": "creator"}
    monkeypatch.setattr(auth_router, "get_user_by_email", lambda _email: user)
    monkeypatch.setattr(auth_router, "validate_login_credentials", lambda *_a, **_k: None)
    monkeypatch.setattr(auth_router, "needs_password_rehash", lambda _hash: False)
    monkeypatch.setattr(auth_router, "touch_user_last_login", lambda _uid: None)
    monkeypatch.setattr(
        auth_router,
        "build_login_payload",
        lambda _user: {"status": "success", "token": token, "user": {"id": STAFF_UID, "email": "staff@example.test"}},
    )


def test_cookie_session_login_sets_httponly_cookie_and_omits_token_from_body(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_login(monkeypatch, "jwt-browser-must-not-see")
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/api/auth/login?session=cookie",
        json={"email": "staff@example.test", "password": "pw-12345678"},
        headers=XHR,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success" and body["user"]["id"] == STAFF_UID
    assert "token" not in body
    assert body["session"] == "cookie"
    assert "jwt-browser-must-not-see" not in response.text
    set_cookie = response.headers["set-cookie"]
    assert f"{security.AUTH_COOKIE_NAME}=jwt-browser-must-not-see" in set_cookie
    assert "HttpOnly" in set_cookie and "SameSite=lax" in set_cookie.lower().replace("samesite", "SameSite")


def test_script_login_without_cookie_flag_still_receives_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_login(monkeypatch, "jwt-for-scripts")
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/api/auth/login", json={"email": "staff@example.test", "password": "pw-12345678"}, headers=XHR)
    assert response.status_code == 200
    assert response.json()["token"] == "jwt-for-scripts"


def test_profile_update_reissues_cookie_but_never_returns_jwt_to_cookie_sessions(
    stack: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        auth_router,
        "_update_user_name",
        lambda _uid, name: {"status": "success", "token": "jwt-reissued", "user": {"id": STAFF_UID, "name": name}},
    )
    client = TestClient(app, raise_server_exceptions=False)
    token = security.make_token(STAFF_UID, "creator", 0)

    browser = client.post(
        "/api/auth/me/profile",
        json={"name": "New Name"},
        headers={**_cookie_header(token), "Authorization": f"Bearer {security.COOKIE_SESSION_MARKER}"},
    )
    assert browser.status_code == 200
    assert "token" not in browser.json()
    assert "jwt-reissued" not in browser.text
    assert f"{security.AUTH_COOKIE_NAME}=jwt-reissued" in browser.headers["set-cookie"]

    script = client.post("/api/auth/me/profile", json={"name": "Script"}, headers={"Authorization": f"Bearer {token}", **XHR})
    assert script.status_code == 200
    assert script.json()["token"] == "jwt-reissued"
