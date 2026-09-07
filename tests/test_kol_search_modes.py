"""Explicit source modes: pure helpers and mocked API/worker dependencies."""
from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_kol_pool_search as route
from app.domains.kol import profile_discovery_pipeline as pipeline
from app.domains.kol.profile_discovery_pipeline_online import DiscoveryOutcome
from app.domains.kol.profile_discovery_pipeline_stages import PlanningState, RecallSetup, RecallState
from app.domains.kol.search_mode import normalize_search_mode


def forbidden(*_args, **_kwargs):
    raise AssertionError("unrequested inventory/provider/queue boundary crossed")


@pytest.mark.parametrize("value,expected", [(None, "hybrid"), ("hybrid", "hybrid"), ("fresh_network", "fresh_network"), (" SAVED ", "saved")])
def test_search_source_mode_is_explicit(value, expected):
    assert normalize_search_mode(value) == expected


@pytest.mark.parametrize("value", ["", "fresh", "discovery", 1, {}, False])
def test_unknown_mode_is_not_guessed(value):
    with pytest.raises(ValueError, match="search_mode"):
        normalize_search_mode(value)


@pytest.mark.parametrize("endpoint", ["smart_kol_search", "smart_kol_search_profile_advance_job"])
def test_fresh_api_accepts_existing_queue_without_inventory_or_inline_provider(monkeypatch, endpoint):
    events = []
    body = {"input": "street night photography", "search_mode": "fresh_network",
            "idempotency_key": "existing-replay-key", "session_id": 88,
            "filters": {"languages": ["en"]}}
    before = deepcopy(body)
    staff = {"id": 7}

    def queue(**kwargs):
        events.append(kwargs)
        return {"status": "queued" if len(events) == 1 else "already_queued",
                "search_session": {"id": 88}, "job_id": 12}

    monkeypatch.setattr(route.kol_profile_discovery, "enqueue_smart_search_profile_advance", queue)
    monkeypatch.setattr(route, "_smart_local_recall", forbidden)
    monkeypatch.setattr(route.kol_profile_recall, "recall_kol_profiles", forbidden)
    monkeypatch.setattr(route.kol_smart_query_planner, "plan_text_query_provider_free", forbidden)
    monkeypatch.setattr(route.kol_profile_discovery, "discover_new_creators", forbidden)
    first = asyncio.run(getattr(route, endpoint)(body=body, staff=staff))
    second = asyncio.run(getattr(route, endpoint)(body=body, staff=staff))
    assert [first["status"], second["status"]] == ["queued", "already_queued"]
    assert first["provider_calls"] is False and first["search_mode"] == "fresh_network"
    assert first["search_session"] == {"id": 88}
    assert first["advance_job"]["job_id"] == second["advance_job"]["job_id"]
    assert "result" not in first  # Queue acceptance is not fabricated empty-result success.
    assert all(event["staff"] is staff for event in events)
    assert all(event["body"]["idempotency_key"] == "existing-replay-key" for event in events)
    assert all(event["body"]["search_mode"] == "fresh_network" for event in events)
    assert body == before


def test_fresh_preserves_queue_denial_instead_of_claiming_success(monkeypatch):
    monkeypatch.setattr(route.kol_profile_discovery, "enqueue_smart_search_profile_advance",
                        lambda **_: {"status": "blocked", "reason": "existing_budget_guard"})
    result = asyncio.run(route.smart_kol_search(body={"input": "street photography", "search_mode": "fresh_network"}, staff={"id": 1}))
    assert result["status"] == "blocked"
    assert result["advance_job"]["reason"] == "existing_budget_guard"
    assert result["provider_calls"] is False


@pytest.mark.parametrize("value", [False, "false", 0])
def test_fresh_conflicting_discovery_flag_does_not_expand_authority(monkeypatch, value):
    monkeypatch.setattr(route.kol_profile_discovery, "enqueue_smart_search_profile_advance", forbidden)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(route.smart_kol_search_profile_advance_job(
            body={"input": "street photography", "search_mode": "fresh_network", "include_new_discovery": value}, staff={"id": 1}))
    assert caught.value.status_code == 400


@pytest.mark.parametrize("endpoint", ["smart_kol_search", "smart_kol_search_profile_advance_job"])
def test_saved_api_calls_local_only_and_never_auto_escalates(monkeypatch, endpoint):
    events = []

    async def session_body(body, *_):
        return body

    async def recall(**kwargs):
        events.append(kwargs)
        return {"items": [{"id": "saved-only"}], "search_session": {"id": 19}}, "street photography"

    monkeypatch.setattr(route, "_smart_search_session_body", session_body)
    monkeypatch.setattr(route, "_smart_local_recall", recall)
    monkeypatch.setattr(route, "_text_response_status", lambda *_: "ready")
    monkeypatch.setattr(route.kol_smart_query_planner, "plan_text_query_provider_free", lambda *_args, **_kwargs: {"status": "ready"})
    monkeypatch.setattr(route.kol_search_escalation, "requested_discovery_payload", forbidden)
    monkeypatch.setattr(route.kol_search_escalation, "auto_escalated_discovery_payload", forbidden)
    monkeypatch.setattr(route.kol_profile_discovery, "enqueue_smart_search_profile_advance", forbidden)
    result = asyncio.run(getattr(route, endpoint)(body={"input": "street photography", "search_mode": "saved", "include_new_discovery": True}, staff={"id": 1}))
    assert len(events) == 1
    assert result["search_mode"] == "saved" and result["new_discovery"] is None
    assert result["new_discovery_status"] == "not_requested" and result["provider_calls"] is False
    assert result["result"]["items"] == [{"id": "saved-only"}]


def test_saved_source_does_not_enter_url_provider_workflow(monkeypatch):
    monkeypatch.setattr(route, "smart_url_search_response", forbidden)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(route.smart_kol_search(body={"input": "https://youtube.com/@example", "search_mode": "saved"}, staff={"id": 1}))
    assert caught.value.status_code == 400


@pytest.fixture
def worker(monkeypatch):
    events, captured = [], {}
    setup = RecallSetup(context={}, recall_kwargs={}, recall_filters={}, resolved_platforms=["youtube"],
                        follower_filter={}, followers_min=None, followers_max=None, follower_source="not_requested",
                        query_cells=[], query_cells_omitted=False)
    monkeypatch.setattr(pipeline.profile_discovery_pipeline_stages, "prepare_plan",
                        lambda **_: PlanningState(query="street photography", operator_query="street photography",
                            operator_anchor={}, operator_platforms=["youtube"], operator_market=""))
    monkeypatch.setattr(pipeline.profile_discovery_pipeline_stages, "prepare_recall", lambda *_: setup)

    def recall(**_):
        events.append("local_recall")
        return {"method": "saved", "items": [], "diagnostics": {}}

    def attach(**kwargs):
        events.append("attach_session")
        captured["recall"] = deepcopy(kwargs["recall_result"])
        return RecallState(result=kwargs["recall_result"], session={"id": 41}, base_count=0, advance_limit=30, smart_local_30=True)

    async def discover(request, **_):
        events.append("online_stage")
        captured["request"] = request
        return DiscoveryOutcome(new_discovery={"status": "partial", "items": []}, base_count=0)

    monkeypatch.setattr(pipeline.targeted_search_runtime, "execute_local_search", recall)
    monkeypatch.setattr(pipeline, "filter_recall_result_platforms", lambda result, *_: result)
    monkeypatch.setattr(pipeline, "filter_recall_result_market", lambda result, *_: result)
    monkeypatch.setattr(pipeline.profile_recall_qualification, "project_smart_local_result", lambda result: result)
    monkeypatch.setattr(pipeline.profile_discovery_pipeline_stages, "attach_recall", attach)
    monkeypatch.setattr(pipeline.profile_discovery_pipeline_online, "run_discovery", discover)
    monkeypatch.setattr(pipeline, "advance_search_session_items", lambda **_: events.append("advance") or {"status": "empty", "selected": 0, "counts": {}})
    monkeypatch.setattr(pipeline, "_enqueue_content_fit", lambda **_: events.append("content_fit"))
    monkeypatch.setattr(pipeline, "_enqueue_video_backfill", lambda **_: events.append("backfill"))
    monkeypatch.setattr(pipeline.derived_job_actor, "derived_job_staff", lambda *_args, **_kwargs: {"id": 1})
    monkeypatch.setattr(pipeline.search_sessions, "update_session_result_summary", lambda *_args, **_kwargs: events.append("summary") or {"id": 41})
    monkeypatch.setattr(pipeline, "_profile_advance_pipeline_status", lambda *_: "partial")
    return events, captured


def test_fresh_worker_inventory_failure_cannot_block_online_stage(monkeypatch, worker):
    events, captured = worker
    monkeypatch.setattr(pipeline.targeted_search_runtime, "execute_local_search", forbidden)
    monkeypatch.setattr(pipeline, "filter_recall_result_market", forbidden)
    payload = {"query_text": "street photography", "search_mode": "fresh_network", "include_field_topup": False,
               "operator_search_spec": {"policy_inputs": {"languages": ["en"], "language_policy": "require"}}}
    original = deepcopy(payload)
    result = asyncio.run(pipeline.execute_smart_search_profile_advance_pipeline(session_id=41, payload=payload))
    assert "local_recall" not in events and "online_stage" in events
    assert events.index("attach_session") < events.index("online_stage")
    assert captured["recall"]["status"] == "not_requested"
    assert captured["recall"]["diagnostics"]["inventory_recall_performed"] is False
    assert captured["request"].base_count == 0
    assert captured["request"].payload["operator_search_spec"] == payload["operator_search_spec"]
    assert result["search_mode"] == "fresh_network" and result["status"] == "partial"
    assert payload == original


def test_saved_worker_never_advances_or_derives_paid_tasks(monkeypatch, worker):
    events, _ = worker
    monkeypatch.setattr(pipeline.profile_discovery_pipeline_online, "run_discovery", forbidden)
    monkeypatch.setattr(pipeline, "advance_search_session_items", forbidden)
    monkeypatch.setattr(pipeline, "_enqueue_content_fit", forbidden)
    monkeypatch.setattr(pipeline, "_enqueue_video_backfill", forbidden)
    result = asyncio.run(pipeline.execute_smart_search_profile_advance_pipeline(
        session_id=41, payload={"query_text": "street photography", "search_mode": "saved", "include_new_discovery": True}))
    assert events == ["local_recall", "attach_session", "summary"]
    assert result["search_mode"] == "saved" and result["provider_calls_performed"] is False
    assert result["new_discovery"] is None and result["advance"]["status"] == "not_requested"


def test_legacy_hybrid_keeps_local_before_online_order(worker):
    events, _ = worker
    result = asyncio.run(pipeline.execute_smart_search_profile_advance_pipeline(
        session_id=41, payload={"query_text": "street photography", "include_field_topup": False}))
    assert events.index("local_recall") < events.index("online_stage")
    assert "advance" in events and "search_mode" not in result


@pytest.mark.parametrize("flag", [False, "false", "OFF", "0", 0, None])
def test_worker_rejects_fresh_discovery_contradiction_before_planning(monkeypatch, flag):
    monkeypatch.setattr(pipeline.profile_discovery_pipeline_stages, "prepare_plan", forbidden)
    with pytest.raises(ValueError, match="conflicts"):
        asyncio.run(pipeline.execute_smart_search_profile_advance_pipeline(
            session_id=41, payload={"query_text": "street photography", "search_mode": "fresh_network", "include_new_discovery": flag}))


def test_saved_mode_is_normalized_before_worker_planner_guard(monkeypatch, worker):
    original = pipeline.profile_discovery_pipeline_stages.prepare_plan

    def check(**kwargs):
        assert kwargs["payload"]["search_mode"] == "saved"
        return original(**kwargs)

    monkeypatch.setattr(pipeline.profile_discovery_pipeline_stages, "prepare_plan", check)
    result = asyncio.run(pipeline.execute_smart_search_profile_advance_pipeline(
        session_id=41, payload={"query_text": "street photography", "search_mode": " SAVED "}))
    assert result["search_mode"] == "saved" and result["provider_calls_performed"] is False


def test_actual_saved_planner_guard_never_attempts_rich_llm(monkeypatch):
    dependencies = pipeline._stage_dependencies()
    guard = {"search_query": "street photography", "status": "ready"}
    monkeypatch.setattr(dependencies.smart_query_planner, "plan_text_query_provider_free", lambda *_args, **_kwargs: guard)
    monkeypatch.setattr(dependencies.smart_query_planner, "plan_text_query", forbidden)
    result, source = pipeline.profile_discovery_pipeline_stages._resolve_worker_plan(
        "street photography", {"search_mode": "saved"}, dependencies)
    assert result == guard and source == "provider_free_saved"
