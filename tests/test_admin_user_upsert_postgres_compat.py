from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routers import admin_common  # noqa: E402


class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _PostgresLikeConn:
    def __init__(self):
        self.statements: list[str] = []
        self.commits = 0

    def execute(self, sql, _params=()):
        normalized = " ".join(str(sql).split())
        self.statements.append(normalized)
        if normalized.startswith("SELECT id, creator_code FROM users"):
            return _Result(None)
        if normalized.startswith("INSERT INTO users"):
            assert "RETURNING id" in normalized
            return _Result({"id": 731})
        if normalized.startswith("SELECT id, created_at, email"):
            return _Result(
                {
                    "id": 731,
                    "created_at": "2026-07-28T00:00:00Z",
                    "email": "audit@example.invalid",
                    "name": "Audit",
                    "creator_code": "V_000731",
                    "status": "approved",
                    "role": "admin",
                    "email_verified": 1,
                    "points_balance": 0,
                    "points_total": 0,
                }
            )
        return _Result()

    def commit(self):
        self.commits += 1


def test_new_admin_user_uses_returning_instead_of_sqlite_lastrowid(monkeypatch):
    conn = _PostgresLikeConn()
    monkeypatch.setattr(admin_common, "get_conn", lambda: conn)
    monkeypatch.setattr(admin_common, "hash_password", lambda _password: "hash")
    monkeypatch.setattr(admin_common, "invalidate_user_cache", lambda _uid: None)
    monkeypatch.setattr(admin_common, "_refresh_user_points_state", lambda *_args, **_kwargs: None)

    result = admin_common._upsert_admin_user_account(
        {
            "email": "audit@example.invalid",
            "password": "temporary",
            "name": "Audit",
            "role": "admin",
            "status": "approved",
        }
    )

    assert result["id"] == 731
    assert any("RETURNING id" in sql for sql in conn.statements)
    assert any("creator_code" in sql and "UPDATE users" in sql for sql in conn.statements)
    assert conn.commits == 1
