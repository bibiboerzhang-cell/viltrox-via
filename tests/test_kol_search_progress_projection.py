from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.domains.kol import search_progress_contract as progress_contract
from app.domains.kol.search_progress_contract import (
    PROGRESS_CONTRACT_SCHEMA,
    observe_worker_health,
    project_search_progress,
)


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _HeartbeatConn:
    def __init__(self, rows: list[dict[str, Any]] | None = None, *, fail: bool = False) -> None:
        self.rows = rows or []
        self.fail = fail

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Rows:
        assert "vkpi_worker_heartbeat" in sql
        assert params == ("redis-worker-%",)
        if self.fail:
            raise RuntimeError("heartbeat table unavailable")
        return _Rows(self.rows)


def _worker(*, online: bool) -> dict[str, Any]:
    return {
        "observed": True,
        "source": "vkpi_worker_heartbeat",
        "state": "online" if online else "offline",
        "online": online,
        "online_count": 1 if online else 0,
        "expected_count": 1,
        "capacity_ready": online,
        "latest_heartbeat_at": "2026-08-03T12:00:00Z" if online else None,
        "reason": "fresh_heartbeat" if online else "no_fresh_apify_worker_heartbeat",
    }


def test_queued_and_running_work_never_inflate_success_progress() -> None:
    session = {
        "status": "running",
        "result_summary": {"phase": "profile", "progress": {"base": 2, "total": 2}},
    }
    items = [
        {
            "id": 1,
            "status": "running",
            "stage": "analysis",
            "kol_pool_id": 101,
            "evidence_id": 501,
            "payload": {
                "profile_execute": {"status": "ready", "kol_pool_id": 101},
                "analysis": {"status": "ready", "cache_id": 9},
                "downstream_jobs": {
                    "video": {"state": "ready", "job_ids": [10]},
                    "comments": {"state": "active", "job_ids": [11]},
                    "audience": {"state": "not_requested", "job_ids": []},
                },
            },
        },
        {
            "id": 2,
            "status": "queued",
            "stage": "profile",
            "payload": {"profile_advance_job": {"status": "queued", "job_id": 12}},
        },
    ]

    result = project_search_progress(session, items, worker_health=_worker(online=False))

    assert result["schema"] == PROGRESS_CONTRACT_SCHEMA
    assert result["state"] == "blocked_by_worker"
    assert result["blocked_by_worker"] is True
    assert result["stages"]["search"]["successful"] == 2
    assert result["stages"]["profile"]["successful"] == 1
    assert result["stages"]["profile"]["counts"]["queued"] == 1
    assert result["stages"]["comments"]["counts"]["active"] == 1
    assert result["active_units"] == 1
    assert result["stages"]["comments"]["data_ready"] is None
    assert result["requested_units"] == 6
    assert result["successful_units"] == 4
    assert result["progress_pct"] == 66.7
    assert result["terminal_pct"] == 66.7


def test_terminal_failure_is_visible_but_never_counted_as_success() -> None:
    session = {"status": "partial", "result_summary": {"progress": {"total": 1}}}
    items = [
        {
            "id": 1,
            "status": "partial",
            "stage": "summary",
            "payload": {
                "profile_execute": {"status": "ready", "kol_pool_id": 101},
                "downstream_jobs": {
                    "video": {"state": "failed", "job_ids": [10]},
                    "comments": {"state": "not_requested", "job_ids": []},
                    "audience": {"state": "not_requested", "job_ids": []},
                },
            },
        }
    ]

    result = project_search_progress(session, items, worker_health=_worker(online=True))

    assert result["state"] == "partial"
    assert result["stages"]["video"]["terminal"] == 1
    assert result["stages"]["video"]["successful"] == 0
    assert result["stages"]["video"]["success_pct"] == 0.0
    assert result["stages"]["video"]["terminal_pct"] == 100.0
    assert result["successful_units"] == 2
    assert result["terminal_units"] == 3
    assert result["progress_pct"] < result["terminal_pct"]
    assert result["full_analysis_complete"] is False


def test_not_requested_optional_stages_do_not_claim_full_analysis() -> None:
    session = {"status": "ready", "result_summary": {"progress": {"total": 1}}}
    items = [
        {
            "id": 1,
            "status": "ready",
            "stage": "summary",
            "kol_pool_id": 101,
            "payload": {"profile_execute": {"status": "ready", "kol_pool_id": 101}},
        }
    ]

    result = project_search_progress(session, items, worker_health=_worker(online=True))

    assert result["progress_pct"] == 100.0
    assert result["stages"]["video"]["state"] == "not_requested"
    assert result["stages"]["comments"]["state"] == "not_requested"
    assert result["stages"]["audience"]["state"] == "not_requested"
    assert result["full_analysis_complete"] is False


def test_30_returned_and_26_audience_is_terminal_requested_work_not_full_analysis() -> None:
    """The user-visible 30/30 + audience 26/26 shape is complete for what was
    requested.  Profile/video/comments were never requested and therefore are
    neither failures nor reasons to keep a spinner alive."""
    session = {"status": "ready", "result_summary": {"phase": "complete", "progress": {"total": 30}}}
    items = []
    for item_id in range(1, 31):
        payload: dict[str, Any] = {}
        if item_id <= 26:
            payload = {
                "audience_preview": {"status": "ready", "sample_size": 50},
                "downstream_jobs": {"audience": {"state": "ready", "job_ids": [1000 + item_id]}},
            }
        items.append({"id": item_id, "status": "ready", "stage": "identified", "payload": payload})

    result = project_search_progress(session, items, worker_health=_worker(online=True))

    assert result["state"] == "ready"
    assert result["requested_units"] == 56
    assert result["successful_units"] == 56
    assert result["requested_tasks_terminal"] is True
    assert result["requested_tasks_successful"] is True
    assert result["completion_kind"] == "requested_stages"
    assert result["not_requested_stages"] == ["profile", "video", "comments"]
    assert result["stages"]["audience"]["successful"] == 26
    assert result["stages"]["audience"]["counts"]["not_requested"] == 4
    assert result["full_analysis_complete"] is False


def test_terminal_empty_partial_session_does_not_fall_back_to_planned() -> None:
    result = project_search_progress(
        {"status": "partial", "result_summary": {"phase": "partial", "progress": {"total": 0}}},
        [],
        worker_health=_worker(online=True),
    )

    assert result["state"] == "partial"
    assert result["requested_units"] == 0
    assert result["requested_tasks_terminal"] is True
    assert result["requested_tasks_successful"] is False
    assert result["completion_kind"] == "empty_result"
    assert result["empty_result"] is True


def test_terminal_empty_ready_session_prefers_empty_result_over_success_label() -> None:
    result = project_search_progress(
        {"status": "ready", "result_summary": {"phase": "complete", "progress": {"total": 0}}},
        [],
        worker_health=_worker(online=True),
    )

    assert result["state"] == "ready"
    assert result["requested_tasks_terminal"] is True
    assert result["requested_tasks_successful"] is True
    assert result["empty_result"] is True
    assert result["completion_kind"] == "empty_result"


def test_active_and_failed_optional_units_remain_separate_from_not_requested() -> None:
    """Production-shape regression: active comments/audience keep the session
    open; video failures make the eventual terminal result partial; the
    unrequested remainder is still explicit and never counted as failed."""
    session = {"status": "running", "result_summary": {"phase": "analysis", "progress": {"total": 33}}}
    items: list[dict[str, Any]] = []
    for item_id in range(1, 34):
        downstream: dict[str, Any] = {}
        payload: dict[str, Any] = {"downstream_jobs": downstream}
        if item_id <= 30:
            payload["profile_execute"] = {"status": "ready", "kol_pool_id": item_id}
        if item_id <= 10:
            downstream["video"] = {"state": "ready"}
        elif item_id <= 14:
            downstream["video"] = {"state": "failed"}
        if item_id <= 18:
            downstream["comments"] = {"state": "ready"}
        elif item_id <= 21:
            downstream["comments"] = {"state": "active"}
        if item_id <= 27:
            downstream["audience"] = {"state": "ready"}
            payload["audience_preview"] = {"status": "ready"}
        elif item_id <= 30:
            downstream["audience"] = {"state": "queued"}
        items.append({"id": item_id, "status": "ready", "stage": "identified", "kol_pool_id": item_id, "payload": payload})

    result = project_search_progress(session, items, worker_health=_worker(online=True))

    assert result["state"] == "active"
    assert result["requested_tasks_terminal"] is False
    assert result["stages"]["profile"]["successful"] == 30
    assert result["stages"]["video"]["counts"] == {
        "ready": 10, "queued": 0, "running": 0, "active": 0, "partial": 0,
        "failed": 4, "skipped": 0, "not_requested": 19, "unknown": 0,
    }
    assert result["stages"]["comments"]["counts"]["active"] == 3
    assert result["stages"]["audience"]["counts"]["queued"] == 3
    assert result["failed_units"] == 4


def test_full_analysis_job_success_is_not_complete_when_comments_data_is_unobservable() -> None:
    session = {"status": "ready", "result_summary": {"progress": {"total": 1}}}
    items = [
        {
            "id": 1,
            "status": "ready",
            "stage": "summary",
            "kol_pool_id": 101,
            "evidence_id": 501,
            "payload": {
                "profile_execute": {"status": "ready", "kol_pool_id": 101},
                "analysis": {"status": "ready"},
                "audience_preview": {"status": "ready", "sample_size": 50},
                "downstream_jobs": {
                    "video": {"state": "ready", "job_ids": [10]},
                    "comments": {"state": "ready", "job_ids": [11]},
                    "audience": {"state": "ready", "job_ids": [12]},
                },
            },
        }
    ]

    result = project_search_progress(session, items, worker_health=_worker(online=True))

    assert result["progress_pct"] == 100.0
    assert result["full_analysis_execution_complete"] is True
    assert result["full_analysis_observable"] is False
    assert result["full_analysis_complete"] is False
    assert result["stages"]["profile"]["data_ready"] == 1
    assert result["stages"]["video"]["data_ready"] == 1
    assert result["stages"]["comments"]["data_ready"] is None
    assert result["stages"]["audience"]["data_ready"] == 1


def test_worker_health_uses_fresh_heartbeats_and_expected_capacity() -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    conn = _HeartbeatConn(
        [
            {
                "worker_name": "apify-1",
                "last_heartbeat_at": now - timedelta(seconds=10),
                "worker_git_sha": "abc",
            },
            {
                "worker_name": "apify-stale",
                "last_heartbeat_at": now - timedelta(minutes=5),
                "worker_git_sha": "abc",
            },
        ]
    )

    result = observe_worker_health(conn, now=now, expected_count=16)

    assert result["observed"] is True
    assert result["online"] is True
    assert result["online_count"] == 1
    assert result["expected_count"] == 16
    assert result["capacity_ready"] is False
    assert result["state"] == "under_capacity"
    assert result["latest_heartbeat_at"] == "2026-08-03T11:59:50Z"


def test_worker_read_failure_is_unknown_not_false_offline() -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)

    result = observe_worker_health(
        _HeartbeatConn(fail=True),
        now=now,
        expected_count=16,
    )

    assert result["observed"] is False
    assert result["state"] == "unknown"
    assert result["online"] is None
    assert result["online_count"] is None
    assert result["reason"] == "heartbeat_unavailable"


def test_worker_capacity_requires_release_sha_alignment_when_release_is_observed(
    monkeypatch,
) -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    release_sha = "a" * 40
    worker_sha = "b" * 40
    monkeypatch.setenv("APP_GIT_SHA", release_sha)
    conn = _HeartbeatConn(
        [
            {
                "worker_name": "apify-1",
                "last_heartbeat_at": now - timedelta(seconds=10),
                "worker_git_sha": worker_sha,
            }
        ]
    )

    mismatch = observe_worker_health(conn, now=now, expected_count=1)

    assert mismatch["online_count"] == 1
    assert mismatch["release_sha"] == release_sha
    assert mismatch["release_sha_source"] == "env:APP_GIT_SHA"
    assert mismatch["worker_sha"] == worker_sha
    assert mismatch["worker_shas"] == [worker_sha]
    assert mismatch["sha_aligned"] is False
    assert mismatch["capacity_ready"] is False
    assert mismatch["state"] == "release_mismatch"

    conn.rows[0]["worker_git_sha"] = release_sha
    aligned = observe_worker_health(conn, now=now, expected_count=1)

    assert aligned["sha_aligned"] is True
    assert aligned["capacity_ready"] is True
    assert aligned["state"] == "online"


def test_worker_sha_alignment_is_unknown_without_safe_release_identity(
    monkeypatch,
    tmp_path,
) -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    monkeypatch.delenv("APP_GIT_SHA", raising=False)
    monkeypatch.setattr(progress_contract, "_PROJECT_ROOT", tmp_path)
    worker_sha = "b" * 40
    conn = _HeartbeatConn(
        [
            {
                "worker_name": "apify-1",
                "last_heartbeat_at": now - timedelta(seconds=10),
                "worker_git_sha": worker_sha,
            }
        ]
    )

    result = observe_worker_health(conn, now=now, expected_count=1)

    assert result["release_sha"] is None
    assert result["release_sha_source"] == "unavailable"
    assert result["worker_sha"] == worker_sha
    assert result["sha_aligned"] is None
    assert result["capacity_ready"] is True


def test_orchestrator_pending_window_is_not_terminal() -> None:
    """会话 1106 案(2026-08-22):召回 30 项先到、全网发现/档案批次尚未登记——仅按会话项证据
    投影得 30/30 ready,前端据此判终态停轮询,一分多钟后落库的发现项再也没被取走。
    编排器已在 progress 里显式写 requested_tasks_terminal=False 且会话仍 running → 契约必须报
    running(orchestration_pending),不得报 ready。"""
    session = {
        "status": "running",
        "result_summary": {
            "phase": "base",
            "progress": {"base": 30, "total": 30, "requested_tasks_terminal": False, "base_complete": True},
        },
    }
    items = [
        {"id": i, "item_type": "recall_candidate", "status": "ready", "stage": "identified", "payload": {}}
        for i in range(1, 31)
    ]

    result = project_search_progress(session, items, worker_health=_worker(online=True))

    assert result["stages"]["search"]["successful"] == 30
    assert result["orchestration_pending"] is True
    assert result["state"] == "running"
    assert result["orchestration_pending_basis"] == "session_running_and_orchestrator_declares_more_tasks"

    # 尚在队列(worker 未接单)→ queued,而非 running
    queued = project_search_progress({**session, "status": "queued"}, items, worker_health=_worker(online=True))
    assert queued["state"] == "queued" and queued["orchestration_pending"] is True

    # worker 离线且编排挂起 → 阻塞(诚实暴露,不是 ready)
    blocked = project_search_progress(session, items, worker_health=_worker(online=False))
    assert blocked["state"] == "blocked_by_worker" and blocked["blocked_by_worker"] is True


def test_orchestrator_pending_flag_ignored_once_session_is_terminal() -> None:
    """管线收尾也写 requested_tasks_terminal=False(下游任务另行登记的旧语义),但会话已
    ready/partial → 不挂起,由会话项自身 queued/running 证据接管;无活跃项即 ready。"""
    session = {
        "status": "ready",
        "result_summary": {"phase": "complete", "progress": {"total": 2, "requested_tasks_terminal": False}},
    }
    items = [
        {"id": 1, "status": "ready", "stage": "summary", "kol_pool_id": 101,
         "payload": {"profile_execute": {"status": "ready", "kol_pool_id": 101}}},
        {"id": 2, "status": "ready", "stage": "summary", "kol_pool_id": 102,
         "payload": {"profile_execute": {"status": "ready", "kol_pool_id": 102}}},
    ]

    result = project_search_progress(session, items, worker_health=_worker(online=True))

    assert result["orchestration_pending"] is False
    assert result["orchestration_pending_basis"] is None
    assert result["state"] == "ready"

    # 缺省/None(旧会话无该键)也不挂起
    legacy = {"status": "running", "result_summary": {"progress": {"total": 2}}}
    assert project_search_progress(legacy, items, worker_health=_worker(online=True))["orchestration_pending"] is False
