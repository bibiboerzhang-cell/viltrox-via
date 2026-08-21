"""Offline release gates for KOL search/video durable provider fences."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from typing import Any

import pytest

from app.domains.kol import provider_job_access as access
from app.domains.kol.video_url_resolver import initial_video_url_resolution_progress
from app.workers import apify_jobs_worker_handlers as handlers
from app.workers import apify_jobs_worker_video_url as video_handler


USER_ID = 34
STAFF_ID = 12
SESSION_ID = 55


def _session(*, owner: int | None = USER_ID, query: str = "camera reviewer") -> dict[str, Any]:
    return {
        "id": SESSION_ID,
        "query_text": query,
        "query_type": "text_recall",
        "status": "running",
        "created_by": owner,
        # Search-session persistence allowlists operator inputs; the queued
        # execution payload below separately seals the richer filter object.
        "input_payload": {},
        "archived_at": None,
    }


def _session_row(*, owner: int | None = USER_ID, query: str = "camera reviewer") -> dict[str, Any]:
    return {
        "id": SESSION_ID,
        "query_text": query,
        "query_type": "text_recall",
        "source": "test",
        "status": "running",
        "created_by": owner,
        "input_payload_json": {"filters": {"languages": ["en"]}},
        "result_summary_json": {},
        "archived_at": None,
        "archived_by": None,
        "archive_reason": "",
        "created_at": "2026-08-21T00:00:00Z",
        "updated_at": "2026-08-21T00:00:00Z",
    }


def _actor(*, active: bool = True, permission: str = "write") -> dict[str, Any]:
    return {
        "id": STAFF_ID,
        "user_id": USER_ID,
        "active": active,
        "suspended_at": None,
        "role": "employee",
        "permissions_json": {"vkpi": permission},
        "user_status": "active",
        "user_email": "operator@example.com",
    }


class _Rows:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    def fetchone(self) -> dict[str, Any] | None:
        return dict(self.row) if self.row is not None else None


class _AccessConn:
    def __init__(
        self,
        *,
        actor: dict[str, Any] | None = None,
        session: dict[str, Any] | None = None,
    ) -> None:
        self.actor = actor if actor is not None else _actor()
        self.session = session if session is not None else _session_row()

    def execute(self, sql: str, _params: tuple[Any, ...] = ()) -> _Rows:
        compact = " ".join(str(sql).split())
        if "FROM staff s" in compact:
            return _Rows(self.actor)
        if "FROM vkpi_kol_search_sessions" in compact:
            return _Rows(self.session)
        raise AssertionError(compact)


class _WorkerCursor:
    def __init__(self, calls: list[tuple[str, tuple[Any, ...]]]) -> None:
        self.calls = calls

    def __enter__(self) -> "_WorkerCursor":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.calls.append((" ".join(str(sql).split()), tuple(params)))


class _WorkerConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    @contextmanager
    def transaction(self):
        yield

    def cursor(self, *_args: Any, **_kwargs: Any) -> _WorkerCursor:
        return _WorkerCursor(self.calls)


def _staff() -> dict[str, Any]:
    return {
        "id": STAFF_ID,
        "user_id": USER_ID,
        "role": "employee",
        "permissions_json": {"vkpi": "write"},
    }


def _smart_payload() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "target_type": "search_session",
        "target_id": str(SESSION_ID),
        "search_session_id": SESSION_ID,
        "query_text": "camera reviewer",
        "filters": {"languages": ["en"]},
        "limit": 30,
        "staff_id": STAFF_ID,
        "triggered_by_user_id": USER_ID,
    }
    payload[access.FENCE_KEY] = access.build_search_session_provider_fence(
        action=access.SMART_SEARCH_PROFILE_ADVANCE,
        session=_session(),
        payload=payload,
        staff=_staff(),
    )
    return payload


def _session_advance_payload() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "target_type": "search_session",
        "target_id": str(SESSION_ID),
        "search_session_id": SESSION_ID,
        "mode": "account_deep",
        "limit": 1,
        "item_ids": [7],
        "staff_id": STAFF_ID,
        "triggered_by_user_id": USER_ID,
    }
    payload[access.FENCE_KEY] = access.build_search_session_provider_fence(
        action=access.SESSION_ADVANCE,
        session=_session(),
        payload=payload,
        staff=_staff(),
    )
    return payload


def _video_payload() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "url": "https://www.youtube.com/watch?v=abcdefghijk",
        "source_url": "https://www.youtube.com/watch?v=abcdefghijk",
        "platform": "youtube",
        "video_id": "abcdefghijk",
        "target_type": "video_url",
        "target_id": "youtube:abcdefghijk",
        "search_session_id": SESSION_ID,
        "max_posts": 3,
        "staff_id": STAFF_ID,
        "triggered_by_user_id": USER_ID,
        "video_url_resolution": initial_video_url_resolution_progress(),
    }
    payload[access.FENCE_KEY] = access.build_video_url_provider_fence(
        payload=payload,
        staff=_staff(),
    )
    return payload


def test_normal_search_fence_revalidates_active_actor_owner_and_query() -> None:
    actor = access.revalidate_provider_job_fence(
        _AccessConn(),
        _smart_payload(),
        expected_action=access.SMART_SEARCH_PROFILE_ADVANCE,
    )
    assert actor["id"] == STAFF_ID
    assert actor["user_id"] == USER_ID


@pytest.mark.parametrize(
    ("conn", "expected"),
    [
        (_AccessConn(actor=_actor(active=False)), "provider_job_actor_inactive"),
        (_AccessConn(actor=_actor(permission="read")), "provider_job_permission_revoked"),
        (_AccessConn(session=_session_row(owner=99)), "search_session_owner_drifted"),
        (
            _AccessConn(session=_session_row(query="transferred query")),
            "search_session_query_drifted",
        ),
    ],
)
def test_search_fence_blocks_revocation_transfer_and_db_query_drift(
    conn: _AccessConn,
    expected: str,
) -> None:
    with pytest.raises(access.ProviderJobAccessError) as raised:
        access.revalidate_provider_job_fence(
            conn,
            _smart_payload(),
            expected_action=access.SMART_SEARCH_PROFILE_ADVANCE,
        )
    assert raised.value.code == expected


def test_payload_query_tamper_invalidates_signed_execution_fingerprint() -> None:
    payload = _smart_payload()
    payload["query_text"] = "tampered query"
    with pytest.raises(access.ProviderJobAccessError) as raised:
        access.revalidate_provider_job_fence(
            _AccessConn(),
            payload,
            expected_action=access.SMART_SEARCH_PROFILE_ADVANCE,
        )
    assert raised.value.code == "provider_job_payload_drifted"


def test_video_url_drift_is_rejected_before_actor_or_provider_resolution() -> None:
    payload = _video_payload()
    payload["url"] = "https://www.youtube.com/watch?v=lmnopqrstuv"
    with pytest.raises(access.ProviderJobAccessError) as raised:
        access.revalidate_provider_job_fence(
            _AccessConn(),
            payload,
            expected_action=access.VIDEO_URL_RESOLVE,
        )
    assert raised.value.code == "video_url_identity_drifted"


def test_http_shaped_dictionary_cannot_forge_server_owned_capability() -> None:
    payload = _video_payload()
    payload.pop(access.FENCE_KEY)
    forged = {
        "action": access.VIDEO_URL_RESOLVE,
        "target_id": payload["target_id"],
        "search_session_id": SESSION_ID,
        "signature": "forged",
    }
    with pytest.raises(access.ProviderJobAccessError) as raised:
        access.build_video_url_provider_fence(
            payload=payload,
            staff=None,
            server_owned_capability=forged,  # type: ignore[arg-type]
        )
    assert raised.value.code == "provider_job_actor_required"


def test_explicit_server_capability_can_only_run_without_a_user_owned_session() -> None:
    payload = _video_payload()
    payload.pop(access.FENCE_KEY)
    payload["search_session_id"] = None
    capability = access.issue_server_owned_provider_capability(
        action=access.VIDEO_URL_RESOLVE,
        target_id=payload["target_id"],
        search_session_id=None,
    )
    payload[access.FENCE_KEY] = access.build_video_url_provider_fence(
        payload=payload,
        staff=None,
        server_owned_capability=capability,
    )
    result = access.revalidate_provider_job_fence(
        _AccessConn(),
        payload,
        expected_action=access.VIDEO_URL_RESOLVE,
    )
    assert result["server_owned"] is True


@pytest.mark.parametrize("kind", ["session", "smart", "video"])
def test_denied_worker_fence_is_terminal_and_provider_bomb_stays_zero(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    app_conn = _AccessConn(actor=_actor(active=False))
    monkeypatch.setattr(access, "get_conn", lambda: app_conn)
    monkeypatch.setattr(access, "db_connection_sync_scope", nullcontext)
    worker_conn = _WorkerConn()
    calls = {"provider": 0}

    def bomb(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls["provider"] += 1
        raise AssertionError("provider/planner must not run after a denied fence")

    if kind == "session":
        monkeypatch.setattr(handlers.kol_profile_discovery, "advance_search_session_items", bomb)
        handlers._process_session_advance(
            worker_conn,
            {"id": 901, "job_type": access.SESSION_ADVANCE},
            _session_advance_payload(),
        )
    elif kind == "smart":
        monkeypatch.setattr(
            handlers.kol_profile_discovery,
            "execute_smart_search_profile_advance_pipeline",
            bomb,
        )
        handlers._process_smart_search_profile_advance(
            worker_conn,
            {"id": 902, "job_type": access.SMART_SEARCH_PROFILE_ADVANCE},
            _smart_payload(),
        )
    else:
        monkeypatch.setattr(video_handler, "run_video_url_resolve_for_job", bomb)
        video_handler._process_video_url_resolve(
            worker_conn,
            {"id": 903, "job_type": access.VIDEO_URL_RESOLVE},
            _video_payload(),
        )

    assert calls["provider"] == 0
    assert len(worker_conn.calls) == 1
    sql, params = worker_conn.calls[0]
    assert "status='blocked'" in sql
    assert "next_retry_at=NULL" in sql
    assert '"provider_calls_performed":false' in str(params[0])
    assert '"retry_allowed":false' in str(params[0])


def test_normal_mock_worker_path_revalidates_then_runs_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(access, "get_conn", lambda: _AccessConn())
    monkeypatch.setattr(access, "db_connection_sync_scope", nullcontext)
    monkeypatch.setattr(
        handlers.kol_search_sessions,
        "update_session_result_summary",
        lambda *_args, **_kwargs: {},
    )
    calls = {"provider": 0}

    async def normal_mock(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls["provider"] += 1
        return {
            "status": "ready",
            "session_id": SESSION_ID,
            "advance": {"status": "ready", "selected": 1, "counts": {}},
            "new_discovery": None,
            "query_plan_source": "mock",
        }

    monkeypatch.setattr(
        handlers.kol_profile_discovery,
        "execute_smart_search_profile_advance_pipeline",
        normal_mock,
    )
    worker_conn = _WorkerConn()
    handlers._process_smart_search_profile_advance(
        worker_conn,
        {"id": 904, "job_type": access.SMART_SEARCH_PROFILE_ADVANCE},
        _smart_payload(),
    )

    assert calls["provider"] == 1
    assert len(worker_conn.calls) == 1
    sql, params = worker_conn.calls[0]
    assert "UPDATE apify_jobs" in sql
    assert params[0] == "done"
    assert params[1] == ""
