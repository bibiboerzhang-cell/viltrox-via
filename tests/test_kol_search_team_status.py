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
        audience_by_pool_id: dict[int, Any] | None = None,
    ) -> None:
        self.sessions = list(sessions)
        self.items = list(items)
        self.population = len(sessions) if population is None else population
        self.staff_population = (
            len({row["created_by"] for row in sessions})
            if staff_population is None
            else staff_population
        )
        self.audience_by_pool_id = dict(audience_by_pool_id or {})
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
        compact = " ".join(sql.split())
        self.calls.append((compact, tuple(params)))
        if compact == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY":
            return _Result()
        if "COUNT(*) AS session_count" in compact:
            return _Result(
                row={
                    "session_count": self.population,
                    "staff_count": self.staff_population,
                    "max_session_id": max(
                        (int(row["id"]) for row in self.sessions),
                        default=0,
                    ),
                }
            )
        if (
            "COUNT(*) AS item_count" in compact
            and "JOIN vkpi_kol_search_sessions AS session" in compact
        ):
            snapshot_max_id = int(params[0])
            eligible_session_ids = {
                int(row["id"])
                for row in self.sessions
                if int(row["id"]) <= snapshot_max_id
            }
            eligible_items = [
                row
                for row in self.items
                if int(row["session_id"]) in eligible_session_ids
            ]
            return _Result(
                row={
                    "item_count": len(eligible_items),
                    "max_item_id": max(
                        (int(row["id"]) for row in eligible_items),
                        default=0,
                    ),
                }
            )
        if (
            "FROM vkpi_kol_search_sessions" in compact
            and "ORDER BY id DESC" in compact
        ):
            if "AND id < ?" in compact:
                snapshot_max_id, before_id, limit = map(int, params)
            else:
                snapshot_max_id, limit = map(int, params)
                before_id = snapshot_max_id + 1
            eligible = [
                row
                for row in self.sessions
                if int(row["id"]) <= snapshot_max_id
                and int(row["id"]) < before_id
            ]
            eligible.sort(key=lambda row: int(row["id"]), reverse=True)
            rows = []
            for row in eligible[:limit]:
                summary = row.get("result_summary_json")
                summary = summary if isinstance(summary, dict) else {}
                progress = summary.get("progress")
                progress = progress if isinstance(progress, dict) else {}
                rows.append(
                    {
                        "id": row["id"],
                        "status": row["status"],
                        "created_by": row["created_by"],
                        "progress_summary_json": {
                            "phase": summary.get("phase"),
                            "progress": {
                                key: progress.get(key)
                                for key in (
                                    "total",
                                    "base",
                                    "requested_tasks_terminal",
                                )
                                if key in progress
                            },
                        },
                    }
                )
            return _Result(rows=rows)
        if compact.startswith("SELECT session_id, COUNT(*) AS item_count"):
            snapshot_max_item_id = int(params[0])
            selected = {int(value) for value in params[1:]}
            counts: dict[int, int] = {}
            for row in self.items:
                session_id = int(row["session_id"])
                if int(row["id"]) <= snapshot_max_item_id and session_id in selected:
                    counts[session_id] = counts.get(session_id, 0) + 1
            return _Result(
                rows=[
                    {"session_id": session_id, "item_count": count}
                    for session_id, count in sorted(counts.items())
                ]
            )
        if "AS progress_payload_json" in compact:
            snapshot_max_item_id = int(params[0])
            selected = {int(value) for value in params[1:]}
            return _Result(
                rows=[
                    {
                        "id": row["id"],
                        "session_id": row["session_id"],
                        "item_type": row["item_type"],
                        "status": row["status"],
                        "stage": row["stage"],
                        "rank": row["rank"],
                        "kol_pool_id": row["kol_pool_id"],
                        "evidence_id": row["evidence_id"],
                        "job_id": row["job_id"],
                        "progress_payload_json": {},
                    }
                    for row in self.items
                    if int(row["id"]) <= snapshot_max_item_id
                    and int(row["session_id"]) in selected
                ]
            )
        if compact.startswith("SELECT id, audience_estimated_json FROM vkpi_kol_pool"):
            selected = {int(value) for value in params}
            return _Result(
                rows=[
                    {
                        "id": profile_id,
                        "audience_estimated_json": audience,
                    }
                    for profile_id, audience in sorted(self.audience_by_pool_id.items())
                    if profile_id in selected
                ]
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


def _build(
    conn: _AggregateConn,
    *,
    limit: int = 500,
    max_scan_sessions: int = team_status.MAX_TEAM_STATUS_SCAN_SESSIONS,
    max_scan_items: int = team_status.MAX_TEAM_STATUS_SCAN_ITEMS,
) -> dict[str, Any]:
    return team_status.build_team_search_status(
        staff={"id": 901, "organization_id": 1},
        limit=limit,
        get_conn_fn=lambda: conn,
        project_progress_fn=_progress_from_id,
        observe_worker_fn=lambda _conn: dict(_WORKER),
        refresh_queue_states_fn=lambda _conn, _items: None,
        canonicalize_items_fn=lambda items: items,
        organization_guard_fn=lambda _staff, _conn: 1,
        max_scan_sessions=max_scan_sessions,
        max_scan_items=max_scan_items,
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
    assert result["sources"] == [
        "vkpi_kol_search_sessions",
        "vkpi_kol_search_session_items",
        "vkpi_kol_pool",
        "apify_jobs",
        "vkpi_worker_heartbeat",
    ]

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
    joined_sql = "\n".join(sql.lower() for sql, _params in conn.calls)
    for forbidden_sql in (
        "select *",
        "query_text",
        "input_payload_json",
        "display_name",
        "handle",
        "contact",
        "email",
        "source_url",
        "dedupe_key",
    ):
        assert forbidden_sql not in joined_sql
    assert "select result_summary_json" not in joined_sql
    assert "select item.payload_json" not in joined_sql
    assert "item.payload_json as progress_payload_json" not in joined_sql


def test_team_status_hydrates_durable_audience_without_reading_profile_pii() -> None:
    conn = _AggregateConn(
        [_session_row(1, status="partial", created_by=71)],
        [_item_row(11, 1)],
        audience_by_pool_id={1011: {"method": "public_metrics"}},
    )
    projected: list[dict[str, Any]] = []

    def project(
        _session: dict[str, Any],
        items: list[dict[str, Any]],
        *,
        worker_health: dict[str, Any],
    ) -> dict[str, Any]:
        assert worker_health == _WORKER
        projected.extend(items)
        return {
            "state": "ready",
            "requested_tasks_terminal": True,
            "blocked_by_worker": False,
            "orchestration_pending": False,
            "full_analysis_complete": False,
        }

    result = team_status.build_team_search_status(
        staff={"id": 901, "organization_id": 1},
        get_conn_fn=lambda: conn,
        project_progress_fn=project,
        observe_worker_fn=lambda _conn: dict(_WORKER),
        refresh_queue_states_fn=lambda _conn, _items: None,
        canonicalize_items_fn=lambda items: items,
        organization_guard_fn=lambda _staff, _conn: 1,
    )

    assert result["counts"]["by_effective_state"] == {"ready": 1}
    assert projected[0]["payload"]["audience_preview"] == {"status": "ready"}
    audience_sql = [
        sql
        for sql, _params in conn.calls
        if "FROM vkpi_kol_pool" in sql
    ]
    assert len(audience_sql) == 1
    assert "audience_estimated_json" in audience_sql[0]
    for forbidden in ("display_name", "handle", "profile_url", "email", "contact"):
        assert forbidden not in audience_sql[0].lower()


def test_limit_is_a_batch_hint_and_keyset_scan_proves_the_full_population() -> None:
    sessions = [
        _session_row(
            session_id,
            status="running" if session_id == 1 else "ready",
            created_by=1000 + session_id,
        )
        for session_id in range(1, 1140)
    ]
    conn = _AggregateConn(sessions, [])

    result = _build(conn, limit=1000)

    assert result["status"] == "ready"
    assert result["coverage"] == {
        "population": 1139,
        "evaluated": 1139,
        "session_population": 1139,
        "staff_population": 1139,
        "evaluated_sessions": 1139,
        "unevaluated_sessions": 0,
        "session_complete": True,
        "session_truncated": False,
        "item_population": 0,
        "evaluated_items": 0,
        "unevaluated_items": 0,
        "item_scan_cap": team_status.MAX_TEAM_STATUS_SCAN_ITEMS,
        "items_complete": True,
        "items_truncated": False,
        "limit": 1000,
        "batch_size": team_status.MAX_TEAM_STATUS_QUERY_BATCH,
        "batches": 5,
        "scan_cap": team_status.MAX_TEAM_STATUS_SCAN_SESSIONS,
        "snapshot_consistent": True,
        "complete": True,
        "truncated": False,
    }
    assert result["counts"]["sessions_evaluated"] == 1139
    assert result["nonterminal"]["all_current_sessions_terminal"] is False
    session_reads = [
        (sql, params)
        for sql, params in conn.calls
        if "FROM vkpi_kol_search_sessions" in sql and "ORDER BY id DESC" in sql
    ]
    assert len(session_reads) == 5
    assert all("OFFSET" not in sql for sql, _params in session_reads)
    assert "AND id < ?" not in session_reads[0][0]
    assert "AND id < ?" in session_reads[1][0]


def test_scan_budget_truncation_never_claims_global_terminal_closure() -> None:
    conn = _AggregateConn(
        [
            _session_row(1, status="running", created_by=71),
            _session_row(2, status="running", created_by=72),
            _session_row(3, status="running", created_by=73),
        ],
        [],
    )

    result = _build(conn, limit=2, max_scan_sessions=2)

    assert result["status"] == "partial"
    assert result["coverage"] == {
        "population": 3,
        "evaluated": 2,
        "session_population": 3,
        "staff_population": 3,
        "evaluated_sessions": 2,
        "unevaluated_sessions": 1,
        "session_complete": False,
        "session_truncated": True,
        "item_population": 0,
        "evaluated_items": 0,
        "unevaluated_items": 0,
        "item_scan_cap": team_status.MAX_TEAM_STATUS_SCAN_ITEMS,
        "items_complete": True,
        "items_truncated": False,
        "limit": 2,
        "batch_size": team_status.MIN_TEAM_STATUS_QUERY_BATCH,
        "batches": 1,
        "scan_cap": 2,
        "snapshot_consistent": True,
        "complete": False,
        "truncated": True,
    }
    assert result["nonterminal"]["all_current_sessions_terminal"] is None


def test_concurrent_restore_changes_membership_and_fails_global_closure() -> None:
    class _RestoreDuringScanConn(_AggregateConn):
        restored = False

        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
            compact = " ".join(sql.split())
            if (
                not self.restored
                and "FROM vkpi_kol_search_sessions" in compact
                and "ORDER BY id DESC" in compact
            ):
                # Restore an older nonterminal session after the opening COUNT.
                # The old population target would stop after ids 4/3/2 and miss 1.
                self.sessions.append(_session_row(1, status="running", created_by=71))
                self.population += 1
                self.staff_population += 1
                self.restored = True
            return super().execute(sql, params)

    conn = _RestoreDuringScanConn(
        [
            _session_row(2, status="ready", created_by=72),
            _session_row(3, status="ready", created_by=73),
            _session_row(4, status="ready", created_by=74),
        ],
        [],
    )

    result = _build(conn, limit=1000)

    assert result["coverage"]["evaluated_sessions"] == 3
    assert result["coverage"]["snapshot_consistent"] is False
    assert result["coverage"]["complete"] is False
    assert result["status"] == "partial"
    assert result["nonterminal"]["all_current_sessions_terminal"] is None


def test_item_scan_budget_stops_on_a_session_boundary_and_never_claims_closure() -> None:
    sessions = [
        _session_row(session_id, status="ready", created_by=70 + session_id)
        for session_id in range(1, 4)
    ]
    items = [_item_row(10 + session_id, session_id) for session_id in range(1, 4)]
    conn = _AggregateConn(sessions, items)

    result = _build(conn, max_scan_items=2)

    assert result["status"] == "partial"
    assert result["coverage"]["population"] == 3
    assert result["coverage"]["evaluated"] == 2
    assert result["coverage"]["item_population"] == 3
    assert result["coverage"]["evaluated_items"] == 2
    assert result["coverage"]["unevaluated_items"] == 1
    assert result["coverage"]["item_scan_cap"] == 2
    assert result["coverage"]["items_complete"] is False
    assert result["coverage"]["items_truncated"] is True
    assert result["coverage"]["complete"] is False
    assert result["coverage"]["truncated"] is True
    assert result["nonterminal"]["observed_count"] == 0
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


def test_postgres_repeatable_read_is_the_first_projection_statement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _AggregateConn([_session_row(1, status="ready", created_by=71)], [])
    guard_calls: list[int] = []
    monkeypatch.setattr(team_status, "is_postgres_runtime", lambda: True)

    result = team_status.build_team_search_status(
        staff={"id": 901, "organization_id": 1},
        get_conn_fn=lambda: conn,
        project_progress_fn=lambda *_args, **_kwargs: {
            "state": "ready",
            "requested_tasks_terminal": True,
        },
        observe_worker_fn=lambda _conn: dict(_WORKER),
        refresh_queue_states_fn=lambda _conn, _items: None,
        canonicalize_items_fn=lambda items: items,
        organization_guard_fn=lambda _staff, _conn: guard_calls.append(len(conn.calls)) or 1,
    )

    assert conn.calls[0][0] == (
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    )
    assert guard_calls == [1]
    assert result["coverage"]["snapshot_consistent"] is True


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


def test_main_application_marks_manager_team_status_private_and_no_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main as main_mod

    staff = {
        "id": 903,
        "staff_id": 903,
        "user_id": 903,
        "role": "manager",
        "is_owner": 0,
        "permissions": {"vkpi": "read"},
    }

    def allow_manager(request: Any) -> bool:
        request.state.vkpi_authorized_staff = staff
        return True

    monkeypatch.setattr(main_mod, "_admin_rbac_allowed_bounded", allow_manager)
    monkeypatch.setattr(main_mod, "_request_requires_db_admission", lambda _request: False)
    monkeypatch.setattr(main_mod, "_audit_sensitive_request", lambda *_args: None)
    monkeypatch.setattr(
        main_mod.main_release_validation,
        "release_validation_active",
        lambda: False,
    )
    monkeypatch.setattr(
        team_status,
        "build_team_search_status",
        lambda *, staff, limit: {
            "schema": team_status.TEAM_STATUS_SCHEMA,
            "status": "ready",
        },
    )
    previous_override = main_mod.app.dependency_overrides.get(get_user_required)
    main_mod.app.dependency_overrides[get_user_required] = lambda: {
        "id": 903,
        "role": "manager",
    }
    try:
        response = TestClient(main_mod.app, raise_server_exceptions=False).get(
            "/api/admin/vkpi/kol-search-sessions/team-status",
            headers={
                "Authorization": "Bearer test-manager",
                "Cookie": "access_token=test-manager",
            },
        )
    finally:
        if previous_override is None:
            main_mod.app.dependency_overrides.pop(get_user_required, None)
        else:
            main_mod.app.dependency_overrides[get_user_required] = previous_override

    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"].lower()
    vary = {
        token.strip().lower()
        for token in response.headers["vary"].split(",")
        if token.strip()
    }
    assert {"authorization", "cookie"}.issubset(vary)


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
