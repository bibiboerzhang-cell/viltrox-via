from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.domains.kol import search_sessions


class _Result:
    def __init__(self, *, row: dict[str, Any] | None = None, rows: list[dict[str, Any]] | None = None):
        self._row = row
        self._rows = rows or []

    def fetchone(self) -> dict[str, Any] | None:
        return self._row

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


def _session_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": 44,
        "query_text": "35mm portrait creator",
        "query_type": "text_recall",
        "source": "smart_kol_input",
        "status": "ready",
        "created_by": 7,
        "input_payload_json": {},
        "result_summary_json": {},
        "approved_kol_ids": [],
        "archived_at": None,
        "archived_by": None,
        "archive_reason": "",
        "created_at": datetime(2026, 7, 12, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 7, 12, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


class _SingleSessionConn:
    def __init__(self, row: dict[str, Any] | None):
        self.row = row
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
        compact = " ".join(sql.split())
        self.calls.append((compact, tuple(params)))
        if compact.startswith("SELECT * FROM vkpi_kol_search_sessions"):
            if not self.row:
                return _Result(row=None)
            if "created_by=?" in compact and int(params[-1]) != int(self.row["created_by"]):
                return _Result(row=None)
            return _Result(row=dict(self.row))
        if "SET archived_at=NOW()" in compact:
            assert self.row is not None
            self.row = {
                **self.row,
                "archived_at": datetime(2026, 7, 13, tzinfo=timezone.utc),
                "archived_by": int(params[0]),
                "archive_reason": str(params[1]),
            }
            return _Result(row=dict(self.row))
        if "SET archived_at=NULL" in compact:
            assert self.row is not None
            self.row = {**self.row, "archived_at": None, "archived_by": None, "archive_reason": ""}
            return _Result(row=dict(self.row))
        if "FROM vkpi_kol_search_session_items" in compact:
            return _Result(rows=[])
        raise AssertionError(f"unexpected SQL: {compact}")

    def commit(self) -> None:
        self.commits += 1


def test_history_listing_is_owner_scoped_and_selects_archive_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    class _ListConn:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[Any, ...]]] = []

        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
            compact = " ".join(sql.split())
            self.calls.append((compact, tuple(params)))
            return _Result(rows=[])

    conn = _ListConn()
    monkeypatch.setattr(search_sessions, "get_conn", lambda: conn)

    search_sessions.list_history(staff={"id": 7}, archived=False)
    search_sessions.list_history(staff={"id": 7}, archived=True)

    active_sql, active_params = conn.calls[0]
    archived_sql, archived_params = conn.calls[1]
    assert "archived_at IS NULL" in active_sql
    assert "archived_at IS NOT NULL" in archived_sql
    assert "created_by=?" in active_sql
    assert active_params[0] == 7
    assert archived_params[0] == 7


def test_history_listing_never_falls_back_to_all_staff_when_actor_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        search_sessions,
        "get_conn",
        lambda: (_ for _ in ()).throw(AssertionError("database must not be read without an actor")),
    )

    result = search_sessions.list_history(staff=None)

    assert result["items"] == []
    assert result["filters"]["scope"] == "current_staff_unresolved"


def test_single_archive_and_restore_preserve_session_and_items(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _SingleSessionConn(_session_row())
    monkeypatch.setattr(search_sessions, "get_conn", lambda: conn)

    archived = search_sessions.archive_history_session(44, staff={"id": 7})
    restored = search_sessions.restore_history_session(44, staff={"id": 7})

    assert archived["archive_status"] == "archived"
    assert archived["archived_at"] is not None
    assert restored["archive_status"] == "restored"
    assert restored["archived_at"] is None
    assert conn.commits == 2
    assert not any("DELETE FROM" in sql for sql, _params in conn.calls)


def test_active_or_other_staff_session_cannot_be_archived(monkeypatch: pytest.MonkeyPatch) -> None:
    active_conn = _SingleSessionConn(_session_row(status="running"))
    monkeypatch.setattr(search_sessions, "get_conn", lambda: active_conn)
    with pytest.raises(ValueError, match="still active"):
        search_sessions.archive_history_session(44, staff={"id": 7})
    assert active_conn.commits == 0

    other_conn = _SingleSessionConn(_session_row(created_by=9))
    monkeypatch.setattr(search_sessions, "get_conn", lambda: other_conn)
    with pytest.raises(LookupError):
        search_sessions.archive_history_session(44, staff={"id": 7})
    assert other_conn.commits == 0


def test_bulk_archive_keeps_running_sessions_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BulkConn:
        def __init__(self) -> None:
            self.commits = 0
            self.calls: list[tuple[str, tuple[Any, ...]]] = []

        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
            compact = " ".join(sql.split())
            self.calls.append((compact, tuple(params)))
            if compact.startswith("UPDATE vkpi_kol_search_sessions"):
                return _Result(rows=[{"id": 41}, {"id": 42}])
            if compact.startswith("SELECT COUNT(*) AS n"):
                return _Result(row={"n": 3})
            raise AssertionError(f"unexpected SQL: {compact}")

        def commit(self) -> None:
            self.commits += 1

    conn = _BulkConn()
    monkeypatch.setattr(search_sessions, "get_conn", lambda: conn)

    result = search_sessions.archive_history_sessions(staff={"id": 7})

    assert result == {
        "status": "archived",
        "archived_count": 2,
        "archived_session_ids": [41, 42],
        "skipped_active_count": 3,
    }
    assert conn.commits == 1
    assert all(params[0] == 7 for _sql, params in conn.calls)


def test_scoped_session_reader_returns_not_found_for_other_staff(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _SingleSessionConn(_session_row(created_by=9))
    monkeypatch.setattr(search_sessions, "get_conn", lambda: conn)

    with pytest.raises(LookupError):
        search_sessions.get_session(44, staff={"id": 7}, scope_to_staff=True)

    assert conn.calls[0][1] == (44, 7)
