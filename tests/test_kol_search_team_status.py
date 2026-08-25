"""Management aggregate contract for cross-employee KOL search visibility.

The new team route is deliberately read-only and aggregate-only.  Managers can
verify whether any current employee search remains nonterminal, while ordinary
staff retain only the existing own-session list and cannot open the team view.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies.auth import get_user_required
from app.api.routers import vkpi_kol_pool_search as router_mod
from app.core import release_validation
from app.domains.access import scope as access_scope
from app.domains.kol import search_sessions_team_status as team_status


class _Result:
    def __init__(
        self,
        *,
        row: dict[str, Any] | None = None,
        rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.row = row
        self.rows = list(rows or [])

    def fetchone(self) -> dict[str, Any] | None:
        return self.row

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self.rows)


def _session_row(session_id: int, *, status: str, created_by: int) -> dict[str, Any]:
    at = datetime(2026, 8, 24, tzinfo=timezone.utc)
    return {
        "id": session_id,
        "query_text": "secret employee search phrase",
        "query_type": "text_recall",
        "source": "smart_kol_input",
        "status": status,
        "created_by": created_by,
        "input_payload_json": {
            "email": "employee-secret@example.test",
            "creator_handle": "@private-creator",
        },
        "result_summary_json": {
            "profile_name": "Private Creator",
            "progress": {"total": 1},
        },
        "approved_kol_ids": [],
        "archived_at": None,
        "archived_by": None,
        "archive_reason": "",
        "created_at": at,
        "updated_at": at,
    }


def _item_row(item_id: int, session_id: int) -> dict[str, Any]:
    at = datetime(2026, 8, 24, tzinfo=timezone.utc)
    return {
        "id": item_id,
        "session_id": session_id,
        "dedupe_key": f"candidate:{item_id}",
        "item_type": "recall_candidate",
        "status": "ready",
        "stage": "summary",
        "rank": 1,
        "score": 90,
        "kol_pool_id": item_id + 1000,
        "evidence_id": None,
        "job_id": None,
        "source_url": "https://example.test/private-creator",
        "payload_json": {
            "display_name": "Private Creator",
            "handle": "@private-creator",
            "contact": "employee-secret@example.test",
        },
        "created_at": at,
        "updated_at": at,
    }


class _AggregateConn:
    def __init__(
        self,
        sessions: list[dict[str, Any]],
        items: list[dict[str, Any]],
        *,
        population: int | None = None,
        staff_population: int | None = None,
    ) -> None:
        self.sessions = list(sessions)
        self.items = list(items)
        self.population = len(sessions) if population is None else population
        self.staff_population = (
            len({row["created_by"] for row in sessions})
            if staff_population is None
            else staff_population
        )
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
        compact = " ".join(sql.split())
        self.calls.append((compact, tuple(params)))
        if "COUNT(*) AS session_count" in compact:
            return _Result(
                row={
                    "session_count": self.population,
                    "staff_count": self.staff_population,
                }
            )
        if compact.startswith("SELECT * FROM vkpi_kol_search_sessions"):
            limit = int(params[0])
            return _Result(rows=self.sessions[:limit])
        if compact.startswith("SELECT * FROM vkpi_kol_search_session_items"):
            selected = {int(value) for value in params}
            return _Result(
                rows=[row for row in self.items if int(row["session_id"]) in selected]
            )
        raise AssertionError(f"unexpected SQL: {compact}")


_WORKER = {
    "observed": True,
    "source": "vkpi_worker_heartbeat",
    "state": "online",
    "online": True,
    "online_count": 16,
    "expected_count": 16,
    "capacity_ready": True,
    "release_sha": "a" * 40,
    "release_sha_source": "env:APP_GIT_SHA",
    "worker_sha": "a" * 40,
    "worker_shas": ["a" * 40],
    "sha_aligned": True,
    "observed_at": "2026-08-24T23:59:00Z",
}


def _progress_from_id(
    session: dict[str, Any],
    _items: list[dict[str, Any]],
    *,
    worker_health: dict[str, Any],
) -> dict[str, Any]:
    assert worker_health == _WORKER
    if int(session["id"]) == 1:
        # Stored partial, but live durable work is still running.
        return {
            "state": "running",
            "requested_tasks_terminal": False,
            "blocked_by_worker": False,
            "orchestration_pending": True,
            "full_analysis_complete": False,
        }
    # Stored running, but the live contract proves all requested tasks terminal.
    return {
        "state": "ready",
        "requested_tasks_terminal": True,
        "blocked_by_worker": False,
        "orchestration_pending": False,
        "full_analysis_complete": True,
    }


def _build(conn: _AggregateConn, *, limit: int = 500) -> dict[str, Any]:
    return team_status.build_team_search_status(
        staff={"id": 901, "organization_id": 1},
        limit=limit,
        get_conn_fn=lambda: conn,
        project_progress_fn=_progress_from_id,
        observe_worker_fn=lambda _conn: dict(_WORKER),
        refresh_queue_states_fn=lambda _conn, _items: None,
        hydrate_previews_fn=lambda _conn, _items: None,
        canonicalize_items_fn=lambda items: items,
        organization_guard_fn=lambda _staff, _conn: 1,
    )


def test_team_status_uses_live_terminal_semantics_and_never_emits_pii() -> None:
    conn = _AggregateConn(
        [
            _session_row(1, status="partial", created_by=71),
            _session_row(2, status="running", created_by=72),
        ],
        [_item_row(11, 1), _item_row(12, 2)],
    )

    result = _build(conn)

    assert result["schema"] == team_status.TEAM_STATUS_SCHEMA
    assert result["scope"] == {
        "mode": "all_staff_in_organization",
        "organization_id": 1,
        "management_only": True,
        "archived_sessions_included": False,
    }
    assert result["coverage"]["complete"] is True
    assert result["counts"]["requested_tasks_terminal"] == 1
    assert result["counts"]["requested_tasks_nonterminal"] == 1
    assert result["counts"]["by_effective_state"] == {"running": 1, "ready": 1}
    assert result["counts"]["by_stored_status"] == {"running": 1, "partial": 1}
    assert result["nonterminal"] == {
        "observed_count": 1,
        "by_effective_state": {"running": 1},
        "all_current_sessions_terminal": False,
    }
    assert result["release_evidence"]["worker_sha_aligned"] is True
    assert result["release_evidence"]["app_release_sha"] == "a" * 40

    serialized = json.dumps(result, ensure_ascii=False)
    for secret in (
        "secret employee search phrase",
        "employee-secret@example.test",
        "Private Creator",
        "private-creator",
        "example.test/private-creator",
    ):
        assert secret not in serialized

    forbidden_keys = {
        "query_text",
        "created_by",
        "staff_id",
        "user_id",
        "display_name",
        "name",
        "handle",
        "source_url",
        "profile_url",
        "input_payload",
        "result_summary",
        "payload",
        "items",
        "session_ids",
    }

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            assert forbidden_keys.isdisjoint(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(result)
    assert all("COMMIT" not in sql.upper() for sql, _params in conn.calls)


def test_truncated_team_status_never_claims_global_terminal_closure() -> None:
    conn = _AggregateConn(
        [
            _session_row(2, status="running", created_by=72),
            _session_row(3, status="running", created_by=73),
        ],
        [],
        population=3,
        staff_population=3,
    )

    result = _build(conn, limit=2)

    assert result["status"] == "partial"
    assert result["coverage"] == {
        "session_population": 3,
        "staff_population": 3,
        "evaluated_sessions": 2,
        "unevaluated_sessions": 1,
        "limit": 2,
        "complete": False,
        "truncated": True,
    }
    assert result["nonterminal"]["all_current_sessions_terminal"] is None


def test_legacy_team_status_fails_closed_outside_default_organization() -> None:
    conn = _AggregateConn([], [])

    with pytest.raises(
        access_scope.ScopeDenied,
        match="KOL search team status organization scope unavailable",
    ):
        team_status.build_team_search_status(
            staff={"id": 901, "organization_id": 2},
            get_conn_fn=lambda: conn,
        )

    assert conn.calls == []


def _client_for_staff(monkeypatch: pytest.MonkeyPatch, staff: dict[str, Any]) -> TestClient:
    from app.api.dependencies import perms as perms_mod

    user = {"id": int(staff["user_id"]), "role": staff.get("role")}
    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/admin/vkpi")
    app.dependency_overrides[get_user_required] = lambda: user
    monkeypatch.setattr(perms_mod, "staff_context_for_user", lambda _user: staff)
    return TestClient(app)


@pytest.mark.parametrize("role", ["manager", "admin"])
def test_manager_and_admin_can_read_team_status(
    monkeypatch: pytest.MonkeyPatch,
    role: str,
) -> None:
    staff = {
        "id": 901,
        "staff_id": 901,
        "user_id": 901,
        "role": role,
        "is_owner": 0,
        "permissions": {"vkpi": "read"},
    }
    captured: dict[str, Any] = {}

    def fake_build(*, staff: dict[str, Any], limit: int) -> dict[str, Any]:
        captured["staff_id"] = staff["id"]
        captured["limit"] = limit
        return {"schema": team_status.TEAM_STATUS_SCHEMA, "status": "ready"}

    monkeypatch.setattr(team_status, "build_team_search_status", fake_build)
    response = _client_for_staff(monkeypatch, staff).get(
        "/api/admin/vkpi/kol-search-sessions/team-status?limit=123"
    )

    assert response.status_code == 200
    assert response.json()["schema"] == team_status.TEAM_STATUS_SCHEMA
    assert captured == {"staff_id": 901, "limit": 123}


def test_staff_is_denied_team_status_but_existing_session_list_stays_own_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staff = {
        "id": 902,
        "staff_id": 902,
        "user_id": 902,
        "role": "employee",
        "is_owner": 0,
        "permissions": {"vkpi": "read"},
    }
    team_calls: list[int] = []
    list_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        team_status,
        "build_team_search_status",
        lambda *, staff, limit: team_calls.append(limit) or {},
    )

    def fake_list_sessions(**kwargs: Any) -> dict[str, Any]:
        list_calls.append(kwargs)
        return {"status": "ready", "count": 0, "items": []}

    monkeypatch.setattr(router_mod.kol_search_sessions, "list_sessions", fake_list_sessions)
    client = _client_for_staff(monkeypatch, staff)

    denied = client.get("/api/admin/vkpi/kol-search-sessions/team-status")
    own = client.get("/api/admin/vkpi/kol-search-sessions")

    assert denied.status_code == 403
    assert denied.json()["detail"] == "management permission required"
    assert team_calls == []
    assert own.status_code == 200
    assert list_calls[0]["scope_to_staff"] is True
    assert list_calls[0]["staff"]["id"] == 902


def test_team_status_is_an_exact_read_only_release_fence_allowance() -> None:
    path = "/api/admin/vkpi/kol-search-sessions/team-status"
    assert release_validation.release_validation_request_allowed("GET", path)
    assert release_validation.release_validation_request_allowed("HEAD", path)
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert not release_validation.release_validation_request_allowed(method, path)
    assert not release_validation.release_validation_request_allowed("GET", f"{path}/extra")
