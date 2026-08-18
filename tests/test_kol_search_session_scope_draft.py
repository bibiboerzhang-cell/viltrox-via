from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_kol_pool_search
from app.domains.kol import search_sessions
from app.domains.projects import cost_estimate
from app.domains.projects import workflow_projects


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
        "query_text": "26mm lens reviewer",
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
        "created_at": datetime(2026, 8, 4, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 8, 4, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


def test_session_list_is_scoped_to_current_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Conn:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[Any, ...]]] = []

        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
            self.calls.append((" ".join(sql.split()), tuple(params)))
            return _Result(rows=[])

    conn = _Conn()
    monkeypatch.setattr(search_sessions, "get_conn", lambda: conn)
    monkeypatch.setattr(search_sessions, "observe_worker_health", lambda _conn: {"status": "ready"})

    result = search_sessions.list_sessions(
        limit=12,
        status="ready",
        staff={"id": 7},
        scope_to_staff=True,
    )

    assert result["items"] == []
    assert "WHERE status=? AND created_by=?" in conn.calls[0][0]
    assert conn.calls[0][1] == ("ready", 7, 12)


class _ApproveConn:
    def __init__(self, *, owner_id: int = 7) -> None:
        self.owner_id = owner_id
        self.commits = 0
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
        compact = " ".join(sql.split())
        self.calls.append((compact, tuple(params)))
        if compact.startswith("SELECT * FROM vkpi_kol_search_sessions"):
            if tuple(params) != (44, self.owner_id):
                return _Result(row=None)
            return _Result(row=_session_row(created_by=self.owner_id))
        if "FROM vkpi_kol_search_session_items i" in compact:
            return _Result(
                rows=[
                    {
                        "kol_pool_id": 101,
                        "item_type": "recall_candidate",
                        "status": "matched",
                        "payload_json": {},
                    },
                ]
            )
        if compact.startswith("UPDATE vkpi_kol_search_sessions"):
            assert params[-2:] == (44, self.owner_id)
            approved = json.loads(str(params[0]))
            return _Result(row=_session_row(created_by=self.owner_id, approved_kol_ids=approved))
        raise AssertionError(f"unexpected SQL: {compact}")

    def commit(self) -> None:
        self.commits += 1


def test_approve_rejects_cross_user_and_skips_non_candidate_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _ApproveConn(owner_id=9)
    monkeypatch.setattr(search_sessions, "get_conn", lambda: conn)

    with pytest.raises(LookupError):
        search_sessions.approve_session(44, kol_pool_ids=[101], staff={"id": 7})
    assert conn.commits == 0

    own_conn = _ApproveConn(owner_id=7)
    monkeypatch.setattr(search_sessions, "get_conn", lambda: own_conn)
    result = search_sessions.approve_session(
        44,
        kol_pool_ids=[101, 999],
        staff={"id": 7},
    )
    assert result["approved_kol_ids"] == [101]
    assert result["skipped_not_in_session"] == [999]
    assert own_conn.commits == 1
    assert any(sql.startswith("UPDATE vkpi_kol_search_sessions") for sql, _ in own_conn.calls)


def test_approve_accepts_pool_backed_recall_and_skips_non_recall_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _ApproveConn(owner_id=7)
    monkeypatch.setattr(search_sessions, "get_conn", lambda: conn)

    result = search_sessions.approve_session(44, kol_pool_ids=[101, 202, 101], staff={"id": 7})

    assert result["approved_kol_ids"] == [101]
    assert result["approved_count"] == 1
    assert result["skipped_not_in_session"] == [202]
    assert conn.commits == 1


def _owned_session(*, summary: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": 44,
        "query_text": "26mm lens reviewer",
        "query_type": "text_recall",
        "status": "ready",
        "created_by": 7,
        "input_payload": {},
        "result_summary": summary or {},
        "approved_kol_ids": [101, 202],
    }


def test_session_cost_and_outreach_ids_can_only_narrow_approved_set() -> None:
    session = _owned_session()

    assert vkpi_kol_pool_search._approved_session_kol_ids(session, None) == [101, 202]
    assert vkpi_kol_pool_search._approved_session_kol_ids(session, [202, 202]) == [202]
    with pytest.raises(HTTPException) as exc:
        vkpi_kol_pool_search._approved_session_kol_ids(session, [101, 999])
    assert exc.value.status_code == 400
    assert "subset of approved" in str(exc.value.detail)


def test_profile_actions_resolve_owned_session_before_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, Any] = {}

    def _get(session_id: int, *, staff: dict[str, Any], scope_to_staff: bool) -> dict[str, Any]:
        observed.update(session_id=session_id, staff=staff, scope_to_staff=scope_to_staff)
        return _owned_session()

    monkeypatch.setattr(search_sessions, "get_session", _get)
    result = vkpi_kol_pool_search._owned_search_session_or_http(44, {"id": 7})

    assert result["id"] == 44
    assert observed == {"session_id": 44, "staff": {"id": 7}, "scope_to_staff": True}


def test_project_draft_rejects_cross_user_and_ignores_body_candidate_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    def _deny(_session_id: int, *, staff: dict[str, Any], scope_to_staff: bool) -> dict[str, Any]:
        assert staff == {"id": 7}
        assert scope_to_staff is True
        raise LookupError("search session not found: 44")

    monkeypatch.setattr(search_sessions, "get_session", _deny)
    with pytest.raises(LookupError):
        workflow_projects.create_project_draft_from_session(44, {}, staff={"id": 7})

    monkeypatch.setattr(search_sessions, "get_session", lambda *_a, **_k: _owned_session())
    class _Conn:
        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
            if "FROM vkpi_kol_search_sessions" in " ".join(sql.split()):
                assert tuple(params) == (44, 7)
                return _Result(row={"id": 44})
            return _Result(row=None)

    monkeypatch.setattr(workflow_projects, "get_conn", lambda: _Conn())
    monkeypatch.setattr(cost_estimate, "estimate_cost_for_kols", lambda *_a, **_k: {})
    monkeypatch.setattr(
        workflow_projects,
        "create_project",
        lambda *_a, **_k: {"id": 9004, "project_uid": "VKPI-SAFE", "stage": "discovery"},
    )
    attached: dict[str, Any] = {}
    monkeypatch.setattr(
        workflow_projects,
        "add_project_kols",
        lambda _project_id, body, **_kwargs: attached.update(body) or {"inserted": 2},
    )
    monkeypatch.setattr(search_sessions, "update_session_result_summary", lambda *_a, **_k: {})

    result = workflow_projects.create_project_draft_from_session(
        44,
        {"kol_pool_ids": [101, 999]},
        staff={"id": 7},
    )

    assert attached["kol_pool_ids"] == [101, 202]
    assert result["requested_kol_count"] == 2


def test_repeated_project_draft_submission_reuses_existing_project(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _owned_session(summary={"draft_project": {"project_id": 9001}})
    monkeypatch.setattr(search_sessions, "get_session", lambda *_a, **_k: session)

    metadata = {
        "search_session_id": 44,
        "brief": {"source": "smart_search"},
        "cost_estimate": {"currency": "USD"},
        "source": {"type": "smart_search_session", "search_session_id": 44},
    }

    class _Conn:
        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
            compact = " ".join(sql.split())
            if "FROM vkpi_kol_search_sessions" in compact:
                assert tuple(params) == (44, 7)
                return _Result(row={"id": 44})
            assert "source_type='smart_search'" in compact
            assert tuple(params) == (9001, 7, 7)
            return _Result(
                row={
                    "id": 9001,
                    "project_uid": "VKPI-SMART",
                    "project_name": "26mm lens reviewer · 合作草案",
                    "stage": "discovery",
                    "source_type": "smart_search",
                    "metadata_json": metadata,
                }
            )

    monkeypatch.setattr(workflow_projects, "get_conn", lambda: _Conn())
    monkeypatch.setattr(
        workflow_projects,
        "create_project",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("retry must not create another project")),
    )
    monkeypatch.setattr(
        workflow_projects,
        "add_project_kols",
        lambda *_a, **_k: {"inserted": 0, "skipped_existing": 2},
    )
    updates: list[dict[str, Any]] = []
    monkeypatch.setattr(
        search_sessions,
        "update_session_result_summary",
        lambda *args, **kwargs: updates.append({"args": args, **kwargs}) or {},
    )

    result = workflow_projects.create_project_draft_from_session(44, {}, staff={"id": 7})

    assert result["project_id"] == 9001
    assert result["reused"] is True
    assert result["attached_kol_count"] == 2
    assert updates[0]["summary_patch"]["draft_project"]["reused"] is True


def test_retry_recovers_project_when_previous_session_summary_write_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search_sessions, "get_session", lambda *_a, **_k: _owned_session())
    calls: list[tuple[str, tuple[Any, ...]]] = []

    class _Conn:
        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
            compact = " ".join(sql.split())
            calls.append((compact, tuple(params)))
            if "FROM vkpi_kol_search_sessions" in compact:
                assert tuple(params) == (44, 7)
                return _Result(row={"id": 44})
            assert "metadata_json->>'search_session_id'=?" in compact
            return _Result(
                row={
                    "id": 9003,
                    "project_uid": "VKPI-RECOVERED",
                    "project_name": "recovered draft",
                    "stage": "discovery",
                    "source_type": "smart_search",
                    "metadata_json": {"search_session_id": 44},
                }
            )

    monkeypatch.setattr(workflow_projects, "get_conn", lambda: _Conn())
    monkeypatch.setattr(
        workflow_projects,
        "create_project",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("metadata recovery must prevent duplicates")),
    )
    monkeypatch.setattr(
        workflow_projects,
        "add_project_kols",
        lambda *_a, **_k: {"inserted": 0, "skipped_existing": 2},
    )
    monkeypatch.setattr(search_sessions, "update_session_result_summary", lambda *_a, **_k: {})

    result = workflow_projects.create_project_draft_from_session(44, {}, staff={"id": 7})

    assert result["project_id"] == 9003
    assert result["reused"] is True
    assert calls[0][1] == (44, 7)
    assert calls[1][1] == ("44", 7, 7)


def test_new_smart_draft_forces_truthful_source_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search_sessions, "get_session", lambda *_a, **_k: _owned_session())

    class _Conn:
        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
            if "FROM vkpi_kol_search_sessions" in " ".join(sql.split()):
                assert tuple(params) == (44, 7)
                return _Result(row={"id": 44})
            return _Result(row=None)

    monkeypatch.setattr(workflow_projects, "get_conn", lambda: _Conn())
    monkeypatch.setattr(cost_estimate, "estimate_cost_for_kols", lambda *_a, **_k: {"currency": "USD"})
    captured: dict[str, Any] = {}

    def _create(body: dict[str, Any], *, staff: dict[str, Any]) -> dict[str, Any]:
        captured.update(body)
        return {"id": 9002, "project_uid": "VKPI-NEW", "stage": "discovery"}

    monkeypatch.setattr(workflow_projects, "create_project", _create)
    monkeypatch.setattr(workflow_projects, "add_project_kols", lambda *_a, **_k: {"inserted": 2})
    monkeypatch.setattr(search_sessions, "update_session_result_summary", lambda *_a, **_k: {})

    result = workflow_projects.create_project_draft_from_session(
        44,
        {"source_type": "manual", "kol_pool_ids": [202]},
        staff={"id": 7},
    )

    assert result["reused"] is False
    assert captured["source_type"] == "smart_search"
    assert captured["metadata"]["search_session_id"] == 44
    assert captured["metadata"]["source"] == {
        "type": "smart_search_session",
        "search_session_id": 44,
        "session_owner_id": 7,
        "query_type": "text_recall",
        "query_text": "26mm lens reviewer",
        "approved_kol_pool_ids": [101, 202],
    }


def test_new_smart_draft_counts_concurrent_existing_and_reports_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search_sessions, "get_session", lambda *_a, **_k: _owned_session())

    class _Conn:
        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
            compact = " ".join(sql.split())
            if "FROM vkpi_kol_search_sessions" in compact:
                assert tuple(params) == (44, 7)
                return _Result(row={"id": 44})
            return _Result(row=None)

    monkeypatch.setattr(workflow_projects, "get_conn", lambda: _Conn())
    monkeypatch.setattr(cost_estimate, "estimate_cost_for_kols", lambda *_a, **_k: {})
    monkeypatch.setattr(
        workflow_projects,
        "create_project",
        lambda *_a, **_k: {
            "id": 9010,
            "project_uid": "VKPI-CONCURRENT",
            "stage": "discovery",
        },
    )
    # The first candidate was attached by a concurrent retry after project
    # creation; the second candidate disappeared meanwhile.
    monkeypatch.setattr(
        workflow_projects,
        "add_project_kols",
        lambda *_a, **_k: {
            "inserted": 0,
            "skipped_existing": 1,
            "missing_kol_pool_ids": [202],
        },
    )
    updates: list[dict[str, Any]] = []
    monkeypatch.setattr(
        search_sessions,
        "update_session_result_summary",
        lambda *args, **kwargs: updates.append({"args": args, **kwargs}) or {},
    )

    result = workflow_projects.create_project_draft_from_session(44, {}, staff={"id": 7})

    assert result["attached_kol_count"] == 1
    assert result["requested_kol_count"] == 2
    assert result["missing_kol_pool_ids"] == [202]
    assert "202" in result["kol_attach_warning"]
    summary = updates[0]["summary_patch"]["draft_project"]
    assert summary["attached_kol_count"] == 1
    assert summary["missing_kol_pool_ids"] == [202]
    assert summary["kol_attach_warning"] == result["kol_attach_warning"]


def test_project_draft_pg_lock_is_owner_scoped_for_update() -> None:
    calls: list[tuple[str, tuple[Any, ...]]] = []

    class _PgContractConn(workflow_projects.PostgresCompatConnection):
        def __init__(self) -> None:
            # The contract only exercises workflow SQL construction; no real
            # pool/raw connection is needed for the overridden execute method.
            pass

        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
            calls.append((" ".join(sql.split()), tuple(params)))
            return _Result(row={"id": 44})

    workflow_projects._lock_owned_search_session_for_draft(
        _PgContractConn(),
        session_id=44,
        owner_id=7,
    )

    assert calls == [
        (
            "SELECT id FROM vkpi_kol_search_sessions WHERE id=? AND created_by=? FOR UPDATE",
            (44, 7),
        )
    ]


def test_projects_board_query_does_not_hide_valid_project_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Conn:
        def __init__(self) -> None:
            self.sql = ""

        def execute(self, sql: str, _params: tuple[Any, ...] = ()) -> _Result:
            self.sql = " ".join(sql.split())
            return _Result(rows=[])

    conn = _Conn()
    monkeypatch.setattr(workflow_projects, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(workflow_projects, "get_conn", lambda: conn)
    monkeypatch.setattr(workflow_projects, "_enrich_project_card_fields", lambda *_a, **_k: None)

    result = workflow_projects.list_projects(staff={"id": 7})

    assert result["projects"] == []
    assert "stage_status <> 'deleted'" in conn.sql
    assert "source_type" not in conn.sql
