from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.domains.kol import search_sessions, search_sessions_history


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


def test_raw_running_effective_terminal_session_remains_non_archivable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _SingleSessionConn(
        _session_row(
            status="running",
            result_summary_json={
                "progress_contract": {
                    "schema": "kol_search_progress_v1",
                    "state": "ready",
                    "requested_tasks_terminal": True,
                }
            },
        )
    )
    monkeypatch.setattr(search_sessions, "get_conn", lambda: conn)

    with pytest.raises(ValueError, match="still active"):
        search_sessions.archive_history_session(44, staff={"id": 7})

    assert conn.commits == 0


def test_legacy_canceled_spelling_can_be_archived(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _SingleSessionConn(_session_row(status="canceled"))
    monkeypatch.setattr(search_sessions, "get_conn", lambda: conn)

    archived = search_sessions.archive_history_session(44, staff={"id": 7})

    assert archived["archive_status"] == "archived"
    assert archived["status"] == "canceled"
    assert conn.commits == 1


def test_bulk_archive_keeps_running_sessions_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BulkConn:
        def __init__(self) -> None:
            self.commits = 0
            self.calls: list[tuple[str, tuple[Any, ...]]] = []

        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
            compact = " ".join(sql.split())
            self.calls.append((compact, tuple(params)))
            if compact.startswith("UPDATE vkpi_kol_search_sessions"):
                return _Result(rows=[{"id": 41}, {"id": 42}, {"id": 46}])
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
        "archived_count": 3,
        "archived_session_ids": [41, 42, 46],
        "skipped_active_count": 3,
    }
    assert conn.commits == 1
    update_sql, update_params = next(
        (sql, params)
        for sql, params in conn.calls
        if sql.startswith("UPDATE vkpi_kol_search_sessions")
    )
    assert "'canceled'" in update_sql
    assert update_params == (7, 7)
    active_sql, active_params = next(
        (sql, params)
        for sql, params in conn.calls
        if sql.startswith("SELECT COUNT(*) AS n")
    )
    assert "'canceled'" in active_sql
    assert active_params == (7,)


def test_scoped_session_reader_returns_not_found_for_other_staff(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _SingleSessionConn(_session_row(created_by=9))
    monkeypatch.setattr(search_sessions, "get_conn", lambda: conn)

    with pytest.raises(LookupError):
        search_sessions.get_session(44, staff={"id": 7}, scope_to_staff=True)

    assert conn.calls[0][1] == (44, 7)


def test_list_history_and_detail_refresh_done_audience_job_without_claiming_empty_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_row = _session_row(
        status="ready",
        result_summary_json={"progress": {"total": 1}},
    )
    item_row = {
        "id": 101,
        "session_id": 44,
        "kol_pool_id": 501,
        "item_type": "existing_kol",
        "status": "ready",
        "stage": "summary",
        "dedupe_key": "existing:501",
        "source_url": "https://instagram.com/creator/",
        "payload_json": {
            "platform": "instagram",
            "handle": "creator",
            "profile_execute": {
                "status": "ready",
                "kol_pool_id": 501,
                "audience_enrichment": {
                    "status": "pending",
                    "queue_status": "queued",
                    "job_id": 77,
                },
            },
            "downstream_jobs": {
                "audience": {"state": "queued", "job_ids": [77]},
            },
        },
    }

    class _ProjectionConn:
        def __init__(self, *, job_status: str = "done") -> None:
            self.calls: list[str] = []
            self.job_status = job_status

        def execute(self, sql: str, _params: tuple[Any, ...] = ()) -> _Result:
            compact = " ".join(sql.split())
            self.calls.append(compact)
            if "FROM vkpi_kol_search_sessions" in compact:
                return _Result(row=dict(session_row), rows=[dict(session_row)])
            if "FROM vkpi_kol_search_session_items" in compact:
                return _Result(rows=[dict(item_row)])
            if "FROM apify_jobs" in compact:
                return _Result(rows=[{"id": 77, "status": self.job_status}])
            if "FROM vkpi_kol_pool" in compact:
                return _Result(
                    rows=[
                        {
                            "id": 501,
                            "display_name": "Creator",
                            "email": "",
                            "contact_channels": {},
                            "other_contacts_json": [],
                            "audience_estimated_json": {},
                            "platform": "instagram",
                            "handle": "creator",
                            "profile_url": "https://instagram.com/creator/",
                            "avatar_url": "",
                            "raw_platform_data": {},
                        }
                    ]
                )
            raise AssertionError(f"unexpected SQL: {compact}")

    worker = {
        "observed": True,
        "online": True,
        "online_count": 1,
        "state": "online",
        "capacity_ready": True,
    }
    monkeypatch.setattr(search_sessions, "observe_worker_health", lambda _conn: worker)
    monkeypatch.setattr(search_sessions_history, "observe_worker_health", lambda _conn: worker)
    monkeypatch.setattr(
        search_sessions,
        "_apply_discovery_account_display_gate",
        lambda items: (items, {"excluded_total": 0, "excluded_own_brand": 0, "excluded_brand_official": 0}),
    )
    monkeypatch.setattr(
        search_sessions,
        "_apply_reach_display_gate",
        lambda _conn, items: (items, {"hidden": 0}),
    )

    detail_conn = _ProjectionConn()
    monkeypatch.setattr(search_sessions, "get_conn", lambda: detail_conn)
    detail = search_sessions.get_session(44)

    list_conn = _ProjectionConn()
    monkeypatch.setattr(search_sessions, "get_conn", lambda: list_conn)
    listed = search_sessions.list_sessions(scope_to_staff=False)["items"][0]

    history_conn = _ProjectionConn()
    history = search_sessions_history.list_history(
        scope_to_staff=False,
        get_conn_fn=lambda: history_conn,
        apply_reach_display_gate_fn=lambda _conn, items: (items, {"hidden": 0}),
        mask_contact_payload_fn=lambda payload: payload,
    )["items"][0]

    detail_contract = detail["progress_contract"]
    list_contract = listed["progress_contract"]
    history_contract = history["progress_contract"]
    assert detail_contract["state"] == list_contract["state"] == history_contract["state"] == "partial"
    assert detail_contract["queued_units"] == list_contract["queued_units"] == history_contract["queued_units"] == 0
    assert detail_contract["stages"]["audience"]["successful"] == 0
    assert list_contract["stages"]["audience"]["counts"]["partial"] == 1
    assert history_contract["stages"]["audience"]["counts"]["partial"] == 1
    assert any("FROM apify_jobs" in sql for sql in detail_conn.calls)
    assert any("FROM apify_jobs" in sql for sql in list_conn.calls)
    assert any("FROM apify_jobs" in sql for sql in history_conn.calls)

    # The list refresh is conservative: live/retryable queue truth wins over
    # stored terminal session status, so real work is never folded to partial.
    running_conn = _ProjectionConn(job_status="running")
    monkeypatch.setattr(search_sessions, "get_conn", lambda: running_conn)
    running = search_sessions.list_sessions(scope_to_staff=False)["items"][0]["progress_contract"]
    assert running["state"] == "running"
    assert running["running_units"] == 1
    assert running["requested_tasks_terminal"] is False

    queued_conn = _ProjectionConn(job_status="queued")
    monkeypatch.setattr(search_sessions, "get_conn", lambda: queued_conn)
    queued = search_sessions.list_sessions(scope_to_staff=False)["items"][0]["progress_contract"]
    assert queued["state"] == "queued"
    assert queued["queued_units"] == 1
    assert queued["requested_tasks_terminal"] is False
