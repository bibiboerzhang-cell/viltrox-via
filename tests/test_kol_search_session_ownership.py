from __future__ import annotations

from typing import Any, Callable

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_activity, vkpi_kol_pool_search
from app.domains.kol import lookup_recovery, search_sessions, unified_search
from app.domains.projects import workflow_projects
from app.services.projects.creator_lifecycle_adapters import (
    DEFAULT_SEARCH_SESSION_DRAFT_PORT,
)


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self.rows)

    def fetchone(self) -> dict[str, Any] | None:
        return dict(self.rows[0]) if self.rows else None


def _session_row(*, session_id: int = 51, created_by: int = 7) -> dict[str, Any]:
    return {
        "id": session_id,
        "query_text": "private launch query",
        "query_type": "text_recall",
        "source": "test",
        "status": "ready",
        "created_by": created_by,
        "input_payload_json": "{}",
        "result_summary_json": "{}",
        "approved_kol_ids": "[]",
    }


def test_list_sessions_is_scoped_to_current_staff(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, tuple[Any, ...]]] = []

    class Conn:
        def execute(self, sql: str, params: tuple[Any, ...]) -> _Cursor:
            captured.append((" ".join(sql.split()), tuple(params)))
            return _Cursor([_session_row()])

    monkeypatch.setattr(search_sessions, "get_conn", lambda: Conn())
    result = search_sessions.list_sessions(limit=12, status="ready", staff={"id": 7})

    assert result["count"] == 1
    session_query = next(
        (sql, params)
        for sql, params in captured
        if "FROM vkpi_kol_search_sessions" in sql
    )
    assert "status=?" in session_query[0]
    assert "created_by=?" in session_query[0]
    assert session_query[1] == ("ready", 7, 12)


def test_list_sessions_unresolved_actor_fails_closed_without_database_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        search_sessions,
        "get_conn",
        lambda: (_ for _ in ()).throw(AssertionError("tenant-wide fallback must not query")),
    )
    assert search_sessions.list_sessions(staff={}) == {
        "status": "ready",
        "count": 0,
        "items": [],
    }


def test_ensure_existing_session_always_fails_closed_without_an_explicit_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def get_session(session_id: int, **kwargs: Any) -> dict[str, Any]:
        calls.append({"session_id": session_id, **kwargs})
        return {"id": session_id}

    monkeypatch.setattr(search_sessions, "get_session", get_session)
    search_sessions.ensure_session_for_result(
        session_id=70,
        create=False,
        query_text="q",
        query_type="text_recall",
        source="test",
        staff={"id": 7},
    )
    search_sessions.ensure_session_for_result(
        session_id=71,
        create=False,
        query_text="q",
        query_type="text_recall",
        source="worker",
        staff=None,
    )
    search_sessions.ensure_session_for_result(
        session_id=72,
        create=False,
        query_text="q",
        query_type="text_recall",
        source="unresolved_request",
        staff={},
    )

    assert calls == [
        {"session_id": 70, "staff": {"id": 7}, "scope_to_staff": True},
        {"session_id": 71, "staff": None, "scope_to_staff": True},
        {"session_id": 72, "staff": {}, "scope_to_staff": True},
    ]


def test_approve_session_cannot_read_or_mutate_foreign_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statements: list[tuple[str, tuple[Any, ...]]] = []

    class Conn:
        def execute(self, sql: str, params: tuple[Any, ...]) -> _Cursor:
            statements.append((" ".join(sql.split()), params))
            return _Cursor([])

    monkeypatch.setattr(search_sessions, "get_conn", lambda: Conn())
    with pytest.raises(LookupError):
        search_sessions.approve_session(51, kol_pool_ids=[9001], staff={"id": 7})

    assert len(statements) == 1
    assert "WHERE id=? AND created_by=?" in statements[0][0]
    assert statements[0][1] == (51, 7)
    assert all(not sql.startswith("UPDATE") for sql, _params in statements)


@pytest.mark.parametrize(
    "invoke",
    [
        lambda: vkpi_kol_pool_search.create_project_draft_from_kol_search_session(
            51, body={}, staff={"id": 7}
        ),
        lambda: vkpi_kol_pool_search.execute_kol_search_session_item_profile_crawl(
            51, 3, body={}, staff={"id": 7}
        ),
        lambda: vkpi_kol_pool_search.advance_kol_search_session_items(
            51, body={}, staff={"id": 7}
        ),
        lambda: vkpi_kol_pool_search.enqueue_kol_search_session_advance(
            51, body={}, staff={"id": 7}
        ),
        lambda: vkpi_kol_pool_search.cancel_kol_search_session_advance(
            51, body={}, staff={"id": 7}
        ),
    ],
)
def test_session_mutation_routes_reject_foreign_id_before_downstream_work(
    monkeypatch: pytest.MonkeyPatch,
    invoke: Callable[[], dict[str, Any]],
) -> None:
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_search_sessions,
        "require_session_owner",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(LookupError("not found")),
    )

    with pytest.raises(HTTPException) as exc_info:
        invoke()
    assert exc_info.value.status_code == 404


def test_lookup_recovery_uses_record_ownership_not_only_tab_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def get_session(session_id: int, **kwargs: Any) -> dict[str, Any]:
        captured.update(session_id=session_id, **kwargs)
        return {"id": session_id, "status": "ready", "result_summary": {}}

    monkeypatch.setattr(lookup_recovery.search_sessions, "get_session", get_session)
    monkeypatch.setattr(
        "app.domains.kol.contact_access.authorize_plaintext_contacts",
        lambda *_args, **_kwargs: True,
    )
    lookup_recovery.recover_session(51, staff={"id": 7})

    assert captured == {
        "session_id": 51,
        "staff": {"id": 7},
        "scope_to_staff": True,
    }


def test_project_draft_reads_only_owned_search_session(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def get_session(session_id: int, **kwargs: Any) -> dict[str, Any]:
        captured.update(session_id=session_id, **kwargs)
        return {"id": session_id, "approved_kol_ids": []}

    monkeypatch.setattr(search_sessions, "get_session", get_session)
    with pytest.raises(ValueError, match="no approved KOLs"):
        workflow_projects.create_project_draft_from_session(
            51,
            {},
            staff={"id": 7},
            search_session_port=DEFAULT_SEARCH_SESSION_DRAFT_PORT,
        )

    assert captured == {
        "session_id": 51,
        "staff": {"id": 7},
        "scope_to_staff": True,
    }


def test_activity_search_source_filters_query_text_by_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class Conn:
        def execute(self, sql: str, params: tuple[Any, ...]) -> _Cursor:
            captured.update(sql=" ".join(sql.split()), params=params)
            return _Cursor([])

    monkeypatch.setattr("app.db.connection.table_exists", lambda _name: True)
    monkeypatch.setattr("app.db.connection.get_conn", lambda: Conn())
    assert vkpi_activity._source_search_sessions(8, staff={"id": 7}) == []
    assert "WHERE created_by=?" in captured["sql"]
    assert captured["params"] == (7, 8)


def test_unified_search_history_count_is_owner_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class Conn:
        def execute(self, sql: str, params: tuple[Any, ...]) -> _Cursor:
            captured.update(sql=" ".join(sql.split()), params=params)
            return _Cursor([{"n": 2}])

    monkeypatch.setattr(unified_search, "table_exists", lambda _name: True)
    monkeypatch.setattr(unified_search, "get_conn", lambda: Conn())
    result = unified_search._history_match("portrait", staff={"id": 7})

    assert result == {"available": True, "prior_sessions": 2, "searched_before": True}
    assert "created_by=?" in captured["sql"]
    assert captured["params"] == ("%portrait%", 7)
