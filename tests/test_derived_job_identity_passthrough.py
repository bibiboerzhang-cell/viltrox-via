"""派生链的身份直通(2026-09-03 LA-identity 车道)。

要钉住的一句话:**worker 里派生出来的付费动作,身份必须从祖父任务 payload 直通下来;
拿不到就不派生**——不是静默入队之后再被授权检查拒,攒一堆 attempts=0 的 blocked 行。

背景实证(本地全量,只读):09-03 那 11 条 `video` 任务 `staff_id=None /
triggered_by_user_id=None / search_session_id=None`,而它们的祖父任务
`smart_search_profile_advance`(job 18546)身份是齐的(`staff_id=40`、
`triggered_by_user_id=1`)。「靠会话反查发起人」已被证伪:worker 在派生**之后**才现建
会话,那批会话 `created_by` 全是 NULL。所以身份只能从 payload 直通。

本文件不碰 `apify_jobs_worker_paid_scope` 的围栏判定(那是问责机制),只证明:
① 解析器的信任级与失败口径;② 三个派生点确实把身份传下去了;③ 无人值守巡检不伪造身份,
改为不派生付费深析。全部零真实 IO(假连接 / monkeypatch)。
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Any

import pytest

from app.domains.kol import derived_job_actor
from app.domains.kol import profile_discovery_pipeline as pipeline
from app.domains.kol import profile_field_topup_enqueue
from app.domains.kol import url_deep_crawl_queue
from app.domains.kol import video_backfill_enqueue
from app.workers import apify_jobs_worker_handlers as handlers


_ACTIVE_STAFF = {
    "id": 40,
    "user_id": 1,
    "role": "admin",
    "active": 1,
    "is_owner": 1,
    "suspended_at": None,
}


class _Row(dict):
    """兼容层把行读回成可 dict() 的映射。"""


class _FakeResult:
    def __init__(self, row: Any) -> None:
        self._row = row

    def fetchone(self) -> Any:
        return self._row


class _StaffConn:
    """只回答本模块用到的两条 staff SELECT,并记录每次查询。"""

    def __init__(self, *, by_id: Any = None, by_user: Any = None) -> None:
        self.by_id = by_id
        self.by_user = by_user
        self.seen: list[str] = []

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> _FakeResult:
        self.seen.append(" ".join(sql.split()))
        if "WHERE id=?" in sql:
            return _FakeResult(self.by_id)
        return _FakeResult(self.by_user)


# ── ① 解析器:信任级与失败口径 ──────────────────────────────────────────────


def test_live_provider_actor_wins_and_costs_zero_queries() -> None:
    conn = _StaffConn()

    staff = derived_job_actor.derived_job_staff(
        {"staff_id": 40, "triggered_by_user_id": 1},
        conn=conn,
        provider_actor=dict(_ACTIVE_STAFF),
    )

    assert staff is not None and staff["id"] == 40
    assert conn.seen == [], "worker 入口刚复核过的活人不该再查一遍库"


def test_server_owned_shell_is_not_a_person() -> None:
    # provider_job_access 的 server_owned 返回值:没有 id,不能拿去铸围栏。
    shell = {"server_owned": True, "staff_id": None, "user_id": None}
    conn = _StaffConn(by_id=_Row(_ACTIVE_STAFF))

    assert derived_job_actor.is_usable_actor(shell) is False
    # 退回 payload 直通:payload 没身份 → None,且一条库都不查。
    assert derived_job_actor.derived_job_staff({}, conn=conn, provider_actor=shell) is None
    assert conn.seen == []


def test_staff_id_resolves_from_parent_payload() -> None:
    conn = _StaffConn(by_id=_Row(_ACTIVE_STAFF))

    staff = derived_job_actor.derived_job_staff(
        {"staff_id": 40, "triggered_by_user_id": 1}, conn=conn
    )

    assert staff is not None
    assert (staff["id"], staff["user_id"]) == (40, 1)
    assert len(conn.seen) == 1 and "FROM staff WHERE id=?" in conn.seen[0]


def test_user_id_is_the_fallback_when_staff_id_is_absent() -> None:
    conn = _StaffConn(by_id=None, by_user=_Row(_ACTIVE_STAFF))

    staff = derived_job_actor.derived_job_staff({"triggered_by_user_id": 1}, conn=conn)

    assert staff is not None and staff["id"] == 40
    assert any("WHERE user_id=?" in sql for sql in conn.seen)


def test_inactive_or_suspended_staff_is_no_identity() -> None:
    left = dict(_ACTIVE_STAFF, active=0)
    suspended = dict(_ACTIVE_STAFF, suspended_at="2026-08-01T00:00:00Z")

    assert derived_job_actor.derived_job_staff(
        {"staff_id": 40}, conn=_StaffConn(by_id=_Row(left))
    ) is None
    assert derived_job_actor.derived_job_staff(
        {"staff_id": 40}, conn=_StaffConn(by_id=_Row(suspended))
    ) is None


def test_boolean_read_back_as_int_one_still_counts_as_active() -> None:
    # 兼容层把 BOOLEAN 读回 1/'t';`is True` 会把它们判成假,解析器必须容错。
    for value in (1, True, "t", "true"):
        conn = _StaffConn(by_id=_Row(dict(_ACTIVE_STAFF, active=value)))
        assert derived_job_actor.derived_job_staff({"staff_id": 40}, conn=conn) is not None


def test_contradicting_ids_refuse_to_guess_an_owner() -> None:
    # payload 说是 user 999,查出来的 staff 属于 user 1 —— 不猜一个人出来背这笔账。
    conn = _StaffConn(by_id=_Row(_ACTIVE_STAFF))

    assert derived_job_actor.derived_job_staff(
        {"staff_id": 40, "triggered_by_user_id": 999}, conn=conn
    ) is None


def test_identity_free_payload_never_touches_the_database() -> None:
    conn = _StaffConn(by_id=_Row(_ACTIVE_STAFF))

    assert derived_job_actor.derived_job_staff(
        {"staff_id": None, "triggered_by_user_id": None}, conn=conn
    ) is None
    assert derived_job_actor.derived_job_staff(None, conn=conn) is None
    assert conn.seen == []


def test_lookup_failure_degrades_to_no_identity_not_an_exception() -> None:
    class _Boom:
        def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("connection lost")

    assert derived_job_actor.derived_job_staff({"staff_id": 40}, conn=_Boom()) is None


def test_no_actor_receipt_is_readable_and_not_a_failure() -> None:
    receipt = derived_job_actor.no_actor_receipt(enqueued=0, items=[])

    assert receipt["status"] == derived_job_actor.NO_ACTOR_STATUS
    assert receipt["reason"] == derived_job_actor.NO_ACTOR_REASON
    assert receipt["enqueued"] == 0
    assert receipt["status"] != "error", "没身份是没事可做,不是失败"
    for jargon in ("LLM", "lexicon", "rule_v0", "embedding", "Qdrant", "Apify", "payload"):
        assert jargon not in receipt["note"]


# ── ② 派生点:身份确实传下去了 ──────────────────────────────────────────────


def test_lazy_backfill_without_an_owner_enqueues_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        video_backfill_enqueue.url_deep_crawl,
        "enqueue_profile_deep_crawl_job",
        lambda *_args, **_kwargs: pytest.fail("无身份时不得派生付费深抓"),
    )
    monkeypatch.setattr(
        video_backfill_enqueue.search_sessions,
        "get_session",
        lambda *_args, **_kwargs: pytest.fail("无身份时连会话都不该去读"),
    )

    result = video_backfill_enqueue.enqueue_lazy_video_backfill_for_session(
        session_id=1124, staff=None
    )

    assert result["status"] == derived_job_actor.NO_ACTOR_STATUS
    assert result["enqueued"] == 0 and result["items"] == []


def test_lazy_backfill_hands_the_owner_to_the_enqueuer(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(
        video_backfill_enqueue.search_sessions,
        "get_session",
        lambda _sid: {"items": [{"item_type": "recall_candidate", "kol_pool_id": 15536}]},
    )
    monkeypatch.setattr(
        video_backfill_enqueue,
        "_eligible_for_backfill",
        lambda *_args, **_kwargs: [{"id": 15536, "profile_url": "https://youtube.com/@x"}],
    )
    monkeypatch.setattr(video_backfill_enqueue, "get_conn", lambda: object())
    monkeypatch.setattr(
        video_backfill_enqueue.url_deep_crawl,
        "enqueue_profile_deep_crawl_job",
        lambda *_args, **kwargs: seen.append(kwargs) or {"status": "queued", "job_id": 1},
    )

    result = video_backfill_enqueue.enqueue_lazy_video_backfill_for_session(
        session_id=1124, staff=dict(_ACTIVE_STAFF)
    )

    assert result["enqueued"] == 1
    assert seen and seen[0]["staff"]["id"] == 40, "派生的深抓必须带上发起人"


def test_enqueuer_writes_the_owner_into_the_child_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """孙任务铸围栏的前提:子任务 payload 里真的有 staff_id / triggered_by_user_id。"""
    captured: dict[str, Any] = {}

    class _Conn:
        def commit(self) -> None:
            captured["committed"] = True

    monkeypatch.setattr(url_deep_crawl_queue, "get_conn", lambda: _Conn())
    monkeypatch.setattr(url_deep_crawl_queue, "_active_profile_job", lambda *_a, **_k: None)
    monkeypatch.setattr(
        url_deep_crawl_queue,
        "enqueue_active_apify_job",
        lambda _conn, *, job_type, payload, idempotency_key: (
            captured.update(job_type=job_type, payload=payload) or ({"id": 4242}, True)
        ),
    )

    result = url_deep_crawl_queue.enqueue_profile_deep_crawl_job(
        "https://youtube.com/@x",
        kol_pool_id=15536,
        staff=dict(_ACTIVE_STAFF),
        queue_lane="batch",
    )

    assert result == {"status": "queued", "job_id": 4242}
    assert captured["payload"]["staff_id"] == 40
    assert captured["payload"]["triggered_by_user_id"] == 1


def test_pipeline_passes_the_resolved_owner_to_both_derivation_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backfill_staff: list[Any] = []
    topup_staff: list[Any] = []
    _install_provider_free_pipeline(monkeypatch)
    monkeypatch.setattr(
        derived_job_actor, "get_conn", lambda: _StaffConn(by_id=_Row(_ACTIVE_STAFF))
    )
    monkeypatch.setattr(
        video_backfill_enqueue,
        "enqueue_lazy_video_backfill_for_session",
        lambda **kwargs: backfill_staff.append(kwargs.get("staff")) or {"status": "ok"},
    )
    monkeypatch.setattr(
        profile_field_topup_enqueue,
        "enqueue_field_topup_for_candidates",
        lambda **kwargs: topup_staff.append(kwargs.get("staff")) or {"status": "ok"},
    )

    asyncio.run(
        pipeline.execute_smart_search_profile_advance_pipeline(
            session_id=1124,
            payload={
                "query_text": "compact autofocus lens reviewers",
                "_worker_planned": True,
                "include_new_discovery": False,
                "include_content_fit": False,
                # 祖父任务(job 18546)payload 的真实形状。
                "staff_id": 40,
                "triggered_by_user_id": 1,
            },
        )
    )

    assert [staff["id"] for staff in backfill_staff] == [40]
    assert [staff["id"] for staff in topup_staff] == [40]


def test_pipeline_without_identity_derives_nothing_paid(monkeypatch: pytest.MonkeyPatch) -> None:
    receipts: list[dict[str, Any]] = []
    _install_provider_free_pipeline(monkeypatch)
    monkeypatch.setattr(derived_job_actor, "get_conn", lambda: _StaffConn())
    monkeypatch.setattr(
        video_backfill_enqueue.url_deep_crawl,
        "enqueue_profile_deep_crawl_job",
        lambda *_args, **_kwargs: pytest.fail("无身份时不得派生付费深抓"),
    )
    monkeypatch.setattr(
        profile_field_topup_enqueue,
        "enqueue_field_topup_for_candidates",
        lambda **kwargs: receipts.append(kwargs) or {"status": "ok"},
    )

    asyncio.run(
        pipeline.execute_smart_search_profile_advance_pipeline(
            session_id=1124,
            payload={
                "query_text": "compact autofocus lens reviewers",
                "_worker_planned": True,
                "include_new_discovery": False,
                "include_content_fit": False,
            },
        )
    )

    assert receipts and receipts[0]["staff"] is None


def test_field_topup_receipt_tells_whether_an_owner_was_attached() -> None:
    without = profile_field_topup_enqueue.enqueue_field_topup_for_candidates(
        candidates=[], staff=None, dry_run=True
    )
    with_owner = profile_field_topup_enqueue.enqueue_field_topup_for_candidates(
        candidates=[], staff=dict(_ACTIVE_STAFF), dry_run=True
    )

    assert without["actor_attached"] is False
    assert with_owner["actor_attached"] is True


# ── ③ 无人值守巡检:不伪造身份,改为不派生付费深析 ────────────────────────────


class _Cursor:
    def __init__(self, sink: list[tuple[str, tuple[Any, ...]]]) -> None:
        self._sink = sink

    def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        self._sink.append((sql, params))

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


class _WorkerConn:
    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple[Any, ...]]] = []

    @contextmanager
    def transaction(self) -> Any:
        yield self

    def cursor(self) -> _Cursor:
        return _Cursor(self.statements)


@contextmanager
def _scope() -> Any:
    yield


def test_auto_poll_refreshes_without_faking_an_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.db import connection as db_connection
    from app.domains.kol import url_deep_crawl

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(handlers, "db_connection_sync_scope", _scope)
    class _PoolConn:
        def execute(self, _sql: str, _params: tuple[Any, ...]) -> _FakeResult:
            return _FakeResult(_Row({"profile_url": "https://youtube.com/@x"}))

    monkeypatch.setattr(db_connection, "get_conn", lambda: _PoolConn())
    monkeypatch.setattr(
        url_deep_crawl,
        "enqueue_profile_deep_crawl_job",
        lambda *_args, **kwargs: calls.append(kwargs) or {"status": "queued", "job_id": 7},
    )

    conn = _WorkerConn()
    payload: dict[str, Any] = {"kol_pool_id": 15536, "mode": "metadata_light"}
    handlers._process_kol_auto_poll(conn, {"id": 99}, payload)

    assert len(calls) == 1
    # 没有真人发起者 → 不编一个出来。
    assert calls[0]["staff"] is None
    # 但也不能让它派生付费深析,否则又是一条 attempts=0 的 blocked。
    assert calls[0]["suppress_final_v1"] is True
    assert payload["auto_poll_result"]["note"] == "metadata_light_refresh_enqueued_profile_only"
    assert conn.statements and "status='done'" in conn.statements[0][0]


# ── 共用:provider-free 的最小管线装置 ────────────────────────────────────────


def _install_provider_free_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pipeline.targeted_search_runtime,
        "prepare_local_search",
        lambda **_kwargs: {
            "recall_filters": {},
            "follower_filter": {"unknown_policy": "pending"},
            "followers_min": None,
            "followers_max": None,
            "follower_source": "not_requested",
            "query_cells": [],
            "query_cells_omitted": False,
            "local_qualification_policy": {},
        },
    )
    monkeypatch.setattr(
        pipeline.targeted_search_runtime,
        "execute_local_search",
        lambda **_kwargs: {
            "method": "characterization",
            "items": [],
            "buckets": {"creator": [], "reviewer": []},
            "diagnostics": {"returned_count": 0, "field_topup_candidates": []},
            "local_qualification": {"returned_count": 0, "shortfall": 30},
        },
    )
    monkeypatch.setattr(pipeline, "filter_recall_result_platforms", lambda result, _v: result)
    monkeypatch.setattr(pipeline, "filter_recall_result_market", lambda result, _v: result)
    monkeypatch.setattr(
        pipeline.profile_recall_qualification, "project_smart_local_result", lambda r: r
    )
    monkeypatch.setattr(
        pipeline.search_sessions, "attach_recall_result", lambda *_a, **_k: {"id": 1124}
    )
    monkeypatch.setattr(
        pipeline,
        "advance_search_session_items",
        lambda **_kwargs: {
            "status": "empty",
            "selected": 0,
            "counts": {},
            "items": [],
            "viltrox_fit_score_changed_ids": [],
        },
    )
    monkeypatch.setattr(pipeline, "_profile_advance_pipeline_status", lambda *_a: "partial")
    monkeypatch.setattr(
        pipeline.search_sessions, "update_session_result_summary", lambda *_a, **_k: {}
    )
