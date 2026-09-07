"""Offline concurrency contracts: no provider, inventory, or database access."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from threading import BoundedSemaphore, Event

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_kol_pool_search as route
from app.domains.kol import profile_discovery_local_lane as local_lane
from app.domains.kol import profile_discovery_pipeline as pipeline
from app.domains.kol import search_sessions
from app.domains.kol.profile_discovery_pipeline_online import DiscoveryOutcome
from app.domains.kol.profile_discovery_pipeline_stages import PlanningState, RecallSetup, RecallState
from app.domains.kol.provider_job_access import ProviderJobAccessError
from app.domains.kol.search_sessions_lanes import merge_lane_summary, write_lane_summary, write_diagnostics_patch
from app.domains.kol.search_sessions_serde import _row_to_session, _sanitize_session_payload


def forbidden(*_args, **_kwargs):
    raise AssertionError("unexpected IO or duplicate execution")


def test_authorized_hybrid_submits_one_existing_job_without_api_inventory(monkeypatch):
    calls = []
    body = {"input": "street photographers", "search_mode": "hybrid", "session_id": 81,
            "include_new_discovery": True, "execute_new_discovery": True,
            "filters": {"languages": ["en"]}}
    original = deepcopy(body)

    def queue(**kwargs):
        calls.append(kwargs)
        return {"status": "queued" if len(calls) == 1 else "already_queued",
                "search_session": {"id": 81}, "job": {"id": 17}}

    monkeypatch.setattr(route.kol_profile_discovery, "enqueue_smart_search_profile_advance", queue)
    monkeypatch.setattr(route, "_smart_text_search", forbidden)
    monkeypatch.setattr(route.kol_smart_query_planner, "plan_text_query_provider_free", forbidden)
    staff = {"id": 7}
    first = asyncio.run(route.smart_kol_search(body=body, staff=staff))
    replay = asyncio.run(route.smart_kol_search(body=body, staff=staff))
    assert [first["status"], replay["status"]] == ["queued", "already_queued"]
    assert all(call["body"]["session_id"] == 81 and call["staff"] is staff for call in calls)
    assert first["advance_job"]["job"] == replay["advance_job"]["job"] == {"id": 17}
    assert first["search_lanes"]["local"]["returned_count"] is None
    assert first["search_lanes"]["online"]["returned_count"] is None
    assert first["provider_calls"] is False and "result" not in first
    assert body == original


@pytest.mark.parametrize("body", [
    {"search_mode": "hybrid"},
    {"search_mode": "hybrid", "include_new_discovery": True, "execute_new_discovery": False},
    {"search_mode": "hybrid", "include_new_discovery": False, "execute_new_discovery": True},
    {"search_mode": "hybrid", "include_new_discovery": "false", "execute_new_discovery": True},
    {"search_mode": "saved", "include_new_discovery": True, "execute_new_discovery": True},
])
def test_unapproved_local_requests_never_enter_new_hybrid_queue(monkeypatch, body):
    async def local(*_):
        return {"status": "ready", "new_discovery": None}
    monkeypatch.setattr(route, "_smart_text_search", local)
    monkeypatch.setattr(route.kol_profile_discovery, "enqueue_smart_search_profile_advance", forbidden)
    result = asyncio.run(route.smart_kol_search(body={"input": "street photographers", **body}, staff={"id": 7}))
    assert result["new_discovery"] is None


def test_hybrid_queue_authorization_failure_is_not_reinterpreted_as_local_success(monkeypatch):
    def denied(**_):
        raise ProviderJobAccessError("provider_job_actor_required", 403)
    monkeypatch.setattr(route.kol_profile_discovery, "enqueue_smart_search_profile_advance", denied)
    monkeypatch.setattr(route, "_smart_text_search", forbidden)
    with pytest.raises(HTTPException):
        asyncio.run(route.smart_kol_search(body={"input": "street photographers", "search_mode": "hybrid",
            "include_new_discovery": True, "execute_new_discovery": True}, staff={"id": 7}))


@pytest.mark.parametrize("flags, expected", [
    ({}, None),
    ({"include_new_discovery": "false", "execute_new_discovery": "true"}, None),
    ({"include_new_discovery": "true", "execute_new_discovery": "false"}, {"status": "planned"}),
    ({"include_discovery": "false", "execute_new_discovery": "false"}, None),
])
def test_explicit_unapproved_hybrid_cannot_fall_through_to_automatic_paid_escalation(monkeypatch, flags, expected):
    async def local(body, query, staff):
        result = route._smart_discovery_payload(
            body=body, recall_query=query, effective_query=query, explicit_platforms=["youtube"],
            staff=staff, session_body={"session_id": 81}, recall_result={"items": []},
        )
        return {"new_discovery": result}
    monkeypatch.setattr(route, "_smart_text_search", local)
    monkeypatch.setattr(route.kol_search_escalation, "auto_escalated_discovery_payload", forbidden)
    monkeypatch.setattr(route.kol_search_escalation.profile_discovery, "discovery_plan", lambda **_: {"status": "planned"})
    monkeypatch.setattr(route.kol_profile_discovery, "enqueue_smart_search_profile_advance", forbidden)
    result = asyncio.run(route.smart_kol_search(body={"input": "street photographers", "search_mode": "hybrid",
        "objective": "prospective_growth", **flags}, staff={"id": 7}))
    assert result["new_discovery"] == expected


@pytest.fixture
def hybrid_worker(monkeypatch):
    events, captures = [], {}
    setup = RecallSetup(context={}, recall_kwargs={}, recall_filters={}, resolved_platforms=["youtube"],
                        follower_filter={}, followers_min=None, followers_max=None, follower_source="not_requested",
                        query_cells=[], query_cells_omitted=False)
    monkeypatch.setattr(pipeline.profile_discovery_pipeline_stages, "prepare_plan",
                        lambda **_: PlanningState(query="street photographers", operator_query="street photographers",
                            operator_anchor={}, operator_platforms=["youtube"], operator_market=""))
    monkeypatch.setattr(pipeline.profile_discovery_pipeline_stages, "prepare_recall", lambda *_: setup)
    monkeypatch.setattr(pipeline, "filter_recall_result_platforms", lambda value, *_: value)
    monkeypatch.setattr(pipeline, "filter_recall_result_market", lambda value, *_: value)
    monkeypatch.setattr(pipeline.profile_recall_qualification, "project_smart_local_result", lambda value: value)
    monkeypatch.setattr(pipeline.search_sessions, "update_search_lane", lambda *_args, **kwargs: events.append(("lane", kwargs)))
    monkeypatch.setattr(pipeline.search_sessions, "update_session_result_summary", lambda *_args, **kwargs: captures.update(final=kwargs) or {"id": 81})

    def attach(**kwargs):
        assert kwargs["lane_only"] is True
        events.append(("attach", deepcopy(kwargs["recall_result"])))
        result = kwargs["recall_result"]
        return RecallState(result=result, session={"id": 81}, base_count=len(result["items"]), advance_limit=30, smart_local_30=True)

    monkeypatch.setattr(pipeline.profile_discovery_pipeline_stages, "attach_recall", attach)
    monkeypatch.setattr(pipeline, "advance_search_session_items", lambda **_: {"status": "ready", "items": [], "selected": 0, "counts": {}})
    monkeypatch.setattr(pipeline, "_enqueue_content_fit", lambda **_: None)
    monkeypatch.setattr(pipeline, "_enqueue_video_backfill", lambda **_: None)
    monkeypatch.setattr(pipeline.derived_job_actor, "derived_job_staff", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(pipeline, "_profile_advance_pipeline_status", lambda *_: "ready")
    payload = {"query_text": "street photographers", "search_mode": "hybrid", "include_new_discovery": True,
               "_smart_local_30_contract": True, "include_field_topup": False}
    return payload, events, captures


def test_online_starts_while_local_is_still_reading(monkeypatch, hybrid_worker):
    payload, events, captures = hybrid_worker
    online_started = Event()
    def read(**_):
        assert online_started.wait(1), "online waited for local instead of starting independently"
        return {"method": "saved", "items": [{"id": 1}], "diagnostics": {}}
    async def online(request, **_):
        assert request.base_count == 0
        online_started.set()
        return DiscoveryOutcome(new_discovery={"status": "ready", "items": [{"id": 2}, {"id": 3}]}, base_count=2)
    monkeypatch.setattr(pipeline.targeted_search_runtime, "execute_local_search", read)
    monkeypatch.setattr(pipeline.profile_discovery_pipeline_online, "run_discovery", online)
    result = asyncio.run(pipeline.execute_smart_search_profile_advance_pipeline(session_id=81, payload=payload))
    assert result["search_lanes"] == {"local": {"status": "ready", "returned_count": 1},
                                      "online": {"status": "ready", "returned_count": 2}}
    assert sum(event[0] == "attach" for event in events) == 1
    assert captures["final"]["summary_patch"]["progress"]["base"] == 3


def test_local_result_is_attached_before_slow_online_finishes(monkeypatch, hybrid_worker):
    payload, events, _ = hybrid_worker
    async def scenario():
        attached = asyncio.Event()
        original = pipeline.profile_discovery_pipeline_stages.attach_recall
        def attach(**kwargs):
            result = original(**kwargs)
            attached.set()
            return result
        async def online(*_args, **_kwargs):
            await asyncio.wait_for(attached.wait(), timeout=1)
            assert any(event[0] == "attach" for event in events)
            return DiscoveryOutcome(new_discovery={"status": "empty", "items": []}, base_count=0)
        monkeypatch.setattr(pipeline.profile_discovery_pipeline_stages, "attach_recall", attach)
        monkeypatch.setattr(pipeline.profile_discovery_pipeline_online, "run_discovery", online)
        monkeypatch.setattr(pipeline.targeted_search_runtime, "execute_local_search", lambda **_: {"items": [{"id": 1}]})
        return await pipeline.execute_smart_search_profile_advance_pipeline(session_id=81, payload=payload)
    assert asyncio.run(scenario())["search_lanes"]["local"]["returned_count"] == 1


def test_local_failure_is_unknown_count_and_does_not_block_online(monkeypatch, hybrid_worker):
    payload, events, _ = hybrid_worker
    def read(**_):
        raise RuntimeError("synthetic inventory failure")
    async def online(*_args, **_kwargs):
        return DiscoveryOutcome(new_discovery={"status": "ready", "items": [{"id": 2}]}, base_count=1)
    monkeypatch.setattr(pipeline.targeted_search_runtime, "execute_local_search", read)
    monkeypatch.setattr(pipeline.profile_discovery_pipeline_online, "run_discovery", online)
    result = asyncio.run(pipeline.execute_smart_search_profile_advance_pipeline(session_id=81, payload=payload))
    assert result["status"] == "partial"
    assert result["search_lanes"]["local"] == {"status": "failed", "returned_count": None}
    assert result["search_lanes"]["online"]["returned_count"] == 1
    assert not any(event[0] == "attach" for event in events)
    assert result["recall"]["returned_count"] is None
    assert result["provider_calls_performed"] is None


def test_online_failure_does_not_erase_local_results_or_claim_overall_success(monkeypatch, hybrid_worker):
    payload, events, _ = hybrid_worker
    async def online(*_args, **_kwargs):
        raise RuntimeError("synthetic online failure")
    monkeypatch.setattr(pipeline.targeted_search_runtime, "execute_local_search", lambda **_: {"items": [{"id": 1}]})
    monkeypatch.setattr(pipeline.profile_discovery_pipeline_online, "run_discovery", online)
    result = asyncio.run(pipeline.execute_smart_search_profile_advance_pipeline(session_id=81, payload=payload))
    assert result["status"] == "partial"
    assert result["search_lanes"]["online"] == {"status": "failed", "returned_count": None}
    assert result["search_lanes"]["local"]["returned_count"] == 1
    assert sum(event[0] == "attach" for event in events) == 1


def test_late_local_result_after_timeout_never_attaches_or_resubmits(monkeypatch, hybrid_worker):
    payload, events, _ = hybrid_worker
    release = Event()
    finished = Event()
    pool = ThreadPoolExecutor(max_workers=2)
    monkeypatch.setattr(local_lane, "_EXECUTOR", pool)
    monkeypatch.setattr(local_lane, "_SLOTS", BoundedSemaphore(2))
    monkeypatch.setattr(local_lane, "LOCAL_LANE_TIMEOUT_SECONDS", 0.01)
    def read(**_):
        assert release.wait(2)
        finished.set()
        return {"items": [{"id": "too-late"}]}
    async def online(*_args, **_kwargs):
        return DiscoveryOutcome(new_discovery={"status": "ready", "items": [{"id": 2}]}, base_count=1)
    monkeypatch.setattr(pipeline.targeted_search_runtime, "execute_local_search", read)
    monkeypatch.setattr(pipeline.profile_discovery_pipeline_online, "run_discovery", online)
    try:
        result = asyncio.run(pipeline.execute_smart_search_profile_advance_pipeline(session_id=81, payload=payload))
        assert result["search_lanes"]["local"] == {"status": "timeout", "returned_count": None}
        before = deepcopy(events)
        release.set()
        assert finished.wait(1)
        pool.shutdown(wait=True)
        assert events == before
        assert not any(event[0] == "attach" for event in events)
    finally:
        release.set()
        pool.shutdown(wait=True)


def test_invalid_shared_plan_never_starts_either_lane(monkeypatch, hybrid_worker):
    payload, events, _ = hybrid_worker
    def invalid(*_):
        raise ValueError("ambiguous_geo_constraints")
    monkeypatch.setattr(pipeline.profile_discovery_pipeline_stages, "prepare_recall", invalid)
    monkeypatch.setattr(pipeline.targeted_search_runtime, "execute_local_search", forbidden)
    monkeypatch.setattr(pipeline.profile_discovery_pipeline_online, "run_discovery", forbidden)
    with pytest.raises(ValueError, match="ambiguous_geo"):
        asyncio.run(pipeline.execute_smart_search_profile_advance_pipeline(session_id=81, payload=payload))
    assert not events


def test_timed_out_reads_keep_both_slots_and_no_third_read_is_queued(monkeypatch):
    pool = ThreadPoolExecutor(max_workers=2)
    release = Event()
    calls = []
    monkeypatch.setattr(local_lane, "_EXECUTOR", pool)
    monkeypatch.setattr(local_lane, "_SLOTS", BoundedSemaphore(2))
    monkeypatch.setattr(local_lane, "LOCAL_LANE_TIMEOUT_SECONDS", 0.01)
    def read():
        calls.append(1)
        assert release.wait(2)
        return {"items": [{"id": "late"}]}
    async def scenario():
        first, second = await asyncio.gather(local_lane.read_local_lane(read), local_lane.read_local_lane(read))
        assert first["status"] == second["status"] == "timeout"
        third = await local_lane.read_local_lane(read)
        assert third["status"] == "capacity_unavailable"
        assert third["diagnostics"]["returned_count"] is None
        assert len(calls) == 2
    try:
        asyncio.run(scenario())
    finally:
        release.set()
        pool.shutdown(wait=True)


@pytest.mark.parametrize("order", [("local", "online"), ("online", "local")])
def test_lane_merges_preserve_each_other_and_global_progress(order):
    baseline = {"phase": "profile", "progress": {"total": 9}, "returned_count": 9}
    result = deepcopy(baseline)
    for lane in order:
        result = merge_lane_summary(result, lane=lane, status="ready", returned_count=1 if lane == "local" else 2,
            summary={"phase": "complete", "progress": {"total": 999}, "returned_count": 999,
                     "local_qualification": {"only": "local"}, "online_qualification": {"only": "online"}})
    assert {key: result[key] for key in baseline} == baseline
    assert result["search_lanes"] == {"local": {"status": "ready", "returned_count": 1},
                                      "online": {"status": "ready", "returned_count": 2}}
    assert result["local_qualification"] == {"only": "local"}
    assert result["online_qualification"] == {"only": "online"}


def test_lane_writer_locks_summary_and_never_updates_global_status():
    calls = []
    class Connection:
        def execute(self, sql, args):
            calls.append((sql, args))
            return self
        def fetchone(self):
            return {"result_summary_json": '{"phase":"profile"}'}
    write_lane_summary(Connection(), 81, lane="local", status="timeout", returned_count=None)
    assert "FOR NO KEY UPDATE" in calls[0][0]
    assert "SET status" not in calls[1][0]
    assert '"returned_count":null' in calls[1][1][0].replace(" ", "")


@pytest.mark.parametrize("diagnostics_first", [True, False])
def test_diagnostics_and_lane_atomic_merges_keep_both_results(diagnostics_first):
    class Connection:
        summary = {"phase": "profile", "progress": {"total": 7}}
        def execute(self, sql, args):
            if sql.startswith("SELECT"):
                assert "FOR NO KEY UPDATE" in sql
            else:
                assert "SET status" not in sql
                self.summary = json.loads(args[0])
            return self
        def fetchone(self):
            return {"result_summary_json": json.dumps(self.summary)}
    conn = Connection()
    lane = lambda: write_lane_summary(conn, 81, lane="local", status="timeout", returned_count=None)
    diagnostics = lambda: write_diagnostics_patch(conn, 81, {"discovery_funnel": {"raw_count": 10},
        "phase": "complete", "progress": {"total": 999}, "search_lanes": {"local": {"status": "ready"}}})
    for update in ((diagnostics, lane) if diagnostics_first else (lane, diagnostics)):
        update()
    assert conn.summary["search_lanes"]["local"] == {"status": "timeout", "returned_count": None}
    assert conn.summary["discovery_funnel"] == {"raw_count": 10}
    assert conn.summary["phase"] == "profile" and conn.summary["progress"] == {"total": 7}


def test_lane_only_recall_bypasses_global_status_updates(monkeypatch):
    captured = []
    monkeypatch.setattr(search_sessions, "record_lane_items", lambda *_args, **kwargs: captured.append(kwargs) or {"items": []})
    monkeypatch.setattr(search_sessions, "record_items", forbidden)
    monkeypatch.setattr(search_sessions, "update_session_result_summary", forbidden)
    search_sessions.attach_recall_result(81, {"items": [], "diagnostics": {}}, lane_only=True)
    assert captured[0]["lane"] == "local" and captured[0]["status"] == "empty"


def test_lane_unknown_count_survives_storage_and_replay_without_restoring_private_fields():
    summary = {"other_null": None, "search_lanes": {
        "local": {"status": "timeout", "returned_count": None, "email": "private@example.test", "raw_payload": {"token": "secret"}},
        "online": {"status": "running", "returned_count": None},
        "unrecognized": {"returned_count": None},
    }}
    stored = _sanitize_session_payload(summary)
    replay = _row_to_session({"id": 81, "result_summary_json": json.dumps(stored)})["result_summary"]
    assert replay == stored
    assert replay["search_lanes"]["local"] == {"status": "timeout", "returned_count": None}
    assert replay["search_lanes"]["online"] == {"status": "running", "returned_count": None}
    assert replay["search_lanes"]["unrecognized"] == {}
    assert "other_null" not in replay


class _LaneStateConnection:
    """In-memory SQL boundary: exercise real summary merge/serialization only."""
    def __init__(self):
        self.summary = {"phase": "base", "diagnostic_marker": "preserve"}
        self.calls = []

    def execute(self, sql, args):
        self.calls.append((sql, args))
        if sql.startswith("UPDATE"):
            self.summary = json.loads(args[0])
        return self

    def fetchone(self):
        return {"result_summary_json": json.dumps(self.summary)}

    def commit(self):
        pass


@pytest.fixture
def durable_hybrid(monkeypatch, hybrid_worker):
    payload, events, captures = hybrid_worker
    conn = _LaneStateConnection()
    monkeypatch.setattr(search_sessions, "get_conn", lambda: conn)
    monkeypatch.setattr(search_sessions, "update_search_lane",
                        lambda sid, **kwargs: write_lane_summary(conn, sid, returned_count=None, **kwargs))
    original_attach = pipeline.profile_discovery_pipeline_stages.attach_recall

    def attach(**kwargs):
        result = original_attach(**kwargs)
        write_lane_summary(conn, 81, lane="local", status="ready", returned_count=result.base_count)
        return result

    monkeypatch.setattr(pipeline.profile_discovery_pipeline_stages, "attach_recall", attach)
    monkeypatch.setattr(pipeline, "advance_search_session_items", forbidden)
    monkeypatch.setattr(pipeline, "_enqueue_content_fit", forbidden)
    monkeypatch.setattr(pipeline, "_enqueue_video_backfill", forbidden)
    return payload, events, captures, conn


@pytest.mark.parametrize("failed_lane", ["local", "online"])
@pytest.mark.parametrize("other_completed", [False, True])
def test_hard_lane_failure_settles_only_unfinished_lanes(monkeypatch, durable_hybrid, failed_lane, other_completed):
    payload, events, captures, conn = durable_hybrid
    failure = ProviderJobAccessError("provider_job_actor_required", 403)

    async def scenario():
        other_started = asyncio.Event()
        other_done = asyncio.Event()
        original_attach = pipeline.profile_discovery_pipeline_stages.attach_recall

        def attach(**kwargs):
            result = original_attach(**kwargs)
            other_done.set()
            return result

        async def branch(lane):
            if lane == failed_lane:
                await other_started.wait()
                if other_completed:
                    await other_done.wait()
                raise failure
            other_started.set()
            if not other_completed:
                await asyncio.Event().wait()

        async def read(_read):
            await branch("local")
            return {"items": [{"id": 1}], "diagnostics": {}}

        async def online(*_args, **_kwargs):
            await branch("online")
            write_lane_summary(conn, 81, lane="online", status="ready", returned_count=1)
            other_done.set()
            return DiscoveryOutcome(new_discovery={"status": "ready", "items": [{"id": 2}]}, base_count=1)

        monkeypatch.setattr(pipeline.profile_discovery_local_lane, "read_local_lane", read)
        monkeypatch.setattr(pipeline.profile_discovery_pipeline_stages, "attach_recall", attach)
        monkeypatch.setattr(pipeline.profile_discovery_pipeline_online, "run_discovery", online)
        with pytest.raises(ProviderJobAccessError) as caught:
            await asyncio.wait_for(pipeline.execute_smart_search_profile_advance_pipeline(session_id=81, payload=payload), 1)
        assert caught.value is failure

    asyncio.run(scenario())
    other_lane = "online" if failed_lane == "local" else "local"
    assert conn.summary["search_lanes"][failed_lane]["status"] == "failed"
    assert conn.summary["search_lanes"][failed_lane]["returned_count"] is None
    if other_completed:
        assert conn.summary["search_lanes"][other_lane] == {
            "status": "ready", "returned_count": 1,
            "execution_id": conn.summary["search_lanes"][failed_lane]["execution_id"],
        }
    else:
        assert conn.summary["search_lanes"][other_lane]["status"] == "failed"
        assert conn.summary["search_lanes"][other_lane]["returned_count"] is None
    assert conn.summary["diagnostic_marker"] == "preserve"
    assert "final" not in captures


@pytest.mark.parametrize("parent_timeout", [False, True])
def test_parent_interruption_settles_running_lanes_without_new_work(monkeypatch, durable_hybrid, parent_timeout):
    payload, events, captures, conn = durable_hybrid

    async def scenario():
        started = {"local": asyncio.Event(), "online": asyncio.Event()}
        stopped = []

        async def branch(lane):
            started[lane].set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.append(lane)

        monkeypatch.setattr(pipeline.profile_discovery_local_lane, "read_local_lane", lambda _read: branch("local"))
        monkeypatch.setattr(pipeline.profile_discovery_pipeline_online, "run_discovery", lambda *_args, **_kwargs: branch("online"))
        task = asyncio.create_task(pipeline.execute_smart_search_profile_advance_pipeline(session_id=81, payload=payload))
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started.values())), 1)
        if parent_timeout:
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(task, 0.01)
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert sorted(stopped) == ["local", "online"]

    asyncio.run(scenario())
    assert {lane["status"] for lane in conn.summary["search_lanes"].values()} == {"failed"}
    assert all(lane["returned_count"] is None for lane in conn.summary["search_lanes"].values())
    assert not events and "final" not in captures
