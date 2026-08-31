"""Behavior locks for the third-wave KOL complexity extractions."""
from __future__ import annotations

from typing import Any

from app.services.kol import account_dossier


class _Cursor:
    def __init__(self, lastrowid: int = 0, row: dict[str, Any] | None = None) -> None:
        self.lastrowid = lastrowid
        self.row = row

    def fetchone(self) -> dict[str, Any] | None:
        return self.row


class _Connection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        self.calls.append((sql, params))
        if "INSERT INTO kol_account_snapshots" in sql:
            return _Cursor(31)
        if "SELECT id FROM kol_posts" in sql:
            return _Cursor(row={"id": 71})
        return _Cursor()

    def commit(self) -> None:
        self.commits += 1


def test_persist_scan_keeps_one_transaction_and_post_comment_order(monkeypatch) -> None:
    conn = _Connection()
    monkeypatch.setattr(account_dossier, "get_conn", lambda: conn)
    result = account_dossier._persist_scan(
        {
            "id": 5,
            "platform": "youtube",
            "handle": "old",
            "avg_views": 111,
            "contact_status": "cold",
        },
        {
            "platform": "youtube",
            "handle": "creator",
            "follower_count": 1234,
            "profile": {
                "avatar_url": "https://img.example/avatar.jpg",
                "profile_url": "https://youtube.com/@creator",
                "contact_emails": ["team@example.com"],
                "contact_links": ["https://creator.example"],
            },
            "posts": [
                {
                    "url": "https://youtube.com/watch?v=abc",
                    "title": "Viltrox lens review",
                    "views": 200,
                    "likes": 20,
                    "comments": 3,
                    "shares": 2,
                    "raw_comments": [{"author": "viewer", "text": "price vs sigma", "likes": 4}],
                },
                {"title": "row without URL", "views": 100},
            ],
        },
    )

    statements = [" ".join(sql.split()) for sql, _params in conn.calls]
    assert statements[0].startswith("INSERT INTO kol_account_snapshots")
    assert next(i for i, sql in enumerate(statements) if sql.startswith("INSERT INTO kol_posts")) < next(
        i for i, sql in enumerate(statements) if sql.startswith("INSERT INTO kol_comments")
    )
    assert statements[-1].startswith("UPDATE kols SET follower_count")
    assert sum(sql.startswith("INSERT INTO kol_posts") for sql in statements) == 1
    assert sum(sql.startswith("INSERT INTO kol_comments") for sql in statements) == 1
    assert conn.commits == 1
    assert result["snapshot_id"] == 31
    assert result["content_count"] == 2
    assert result["avg_views"] == 150
    assert result["contact_email"] == "team@example.com"
    assert result["contact_links"] == ["https://creator.example"]
