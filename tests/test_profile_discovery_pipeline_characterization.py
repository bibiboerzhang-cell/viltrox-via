from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

import pytest

from app.domains.kol import profile_discovery_pipeline as pipeline


def _install_provider_free_happy_path(
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
) -> None:
    def _prepare_local_search(**_kwargs: Any) -> dict[str, Any]:
        events.append("prepare_local")
        return {
            "recall_filters": {},
            "follower_filter": {"unknown_policy": "pending"},
            "followers_min": None,
            "followers_max": None,
            "follower_source": "not_requested",
            "query_cells": [],
            "query_cells_omitted": False,
            "local_qualification_policy": {},
        }

    def _execute_local_search(**kwargs: Any) -> dict[str, Any]:
        events.append("execute_local")
        assert kwargs["recall"] is pipeline.profile_recall.recall_kol_profiles
        return {
            "method": "characterization",
            "items": [],
            "buckets": {"creator": [], "reviewer": []},
            "diagnostics": {"returned_count": 0},
            "local_qualification": {"returned_count": 0, "shortfall": 30},
        }

    monkeypatch.setattr(
        pipeline.targeted_search_runtime,
        "prepare_local_search",
        _prepare_local_search,
    )
    monkeypatch.setattr(
        pipeline.targeted_search_runtime,
        "execute_local_search",
        _execute_local_search,
    )
    monkeypatch.setattr(
        pipeline,
        "filter_recall_result_platforms",
        lambda result, _value: result,
    )
    monkeypatch.setattr(
        pipeline,
        "filter_recall_result_market",
        lambda result, _value: result,
    )
    monkeypatch.setattr(
        pipeline.profile_recall_qualification,
        "project_smart_local_result",
        lambda result: result,
    )
    monkeypatch.setattr(
        pipeline.search_sessions,
        "attach_recall_result",
        lambda _session_id, _result: events.append("attach_recall") or {"id": 17},
    )
    monkeypatch.setattr(
        pipeline,
        "advance_search_session_items",
        lambda **_kwargs: events.append("advance")
        or {
            "status": "empty",
            "selected": 0,
            "counts": {},
            "items": [],
            "viltrox_fit_score_changed_ids": [],
        },
    )
    monkeypatch.setattr(
        pipeline,
        "_profile_advance_pipeline_status",
        lambda *_args: "partial",
    )
    monkeypatch.setattr(
        pipeline.search_sessions,
        "update_session_result_summary",
        lambda *_args, **_kwargs: events.append("summary") or {},
    )


def test_provider_free_lane_preserves_stage_order_and_caller_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    _install_provider_free_happy_path(monkeypatch, events)

    async def _forbidden_provider(**_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("online provider must stay disabled")

    monkeypatch.setattr(pipeline, "discover_new_creators", _forbidden_provider)
    payload = {
        "query_text": "portrait filmmakers using compact autofocus lenses",
        "_worker_planned": True,
        "include_new_discovery": False,
        "include_content_fit": False,
        "include_lazy_video_backfill": False,
        "include_field_topup": False,
        "filters": {"languages": ["en"]},
    }
    before = deepcopy(payload)

    result = asyncio.run(
        pipeline.execute_smart_search_profile_advance_pipeline(
            session_id=17,
            payload=payload,
        )
    )

    assert events == [
        "prepare_local",
        "execute_local",
        "attach_recall",
        "advance",
        "summary",
    ]
    assert payload == before
    assert result["status"] == "partial"
    assert result["new_discovery"] is None


def test_missing_query_fails_before_any_session_write_or_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("pipeline crossed the missing-query boundary")

    monkeypatch.setattr(
        pipeline.search_sessions,
        "update_session_result_summary",
        _forbidden,
    )
    monkeypatch.setattr(pipeline, "discover_new_creators", _forbidden)

    with pytest.raises(ValueError, match="missing query_text"):
        asyncio.run(
            pipeline.execute_smart_search_profile_advance_pipeline(
                session_id=18,
                payload={},
            )
        )


def test_product_persona_failure_is_visible_without_sensitive_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.costs import product_persona

    def _fail_persona(_product_sku: str) -> dict[str, Any]:
        raise RuntimeError("provider detail must not enter logs")

    warnings: list[tuple[Any, ...]] = []
    monkeypatch.setattr(product_persona, "get_product_persona", _fail_persona)
    monkeypatch.setattr(
        pipeline.logger,
        "warning",
        lambda *args: warnings.append(args),
    )

    assert pipeline._load_product_persona("private-sku") == {}
    assert warnings == [
        (
            "smart search product persona unavailable | error_type=%s",
            "RuntimeError",
        )
    ]
    serialized = repr(warnings)
    assert "private-sku" not in serialized
    assert "provider detail" not in serialized


def test_catalog_clarification_stops_before_recall_advance_and_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domains.kol import smart_query_planner

    events: list[str] = []
    plan = {
        "status": "needs_clarification",
        "reason": "unknown_product",
        "search_query": "",
    }
    monkeypatch.setattr(
        smart_query_planner,
        "plan_text_query_provider_free",
        lambda *_args, **_kwargs: dict(plan),
    )
    monkeypatch.setattr(
        smart_query_planner,
        "plan_text_query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("rich planner must not win")),
    )
    monkeypatch.setattr(
        pipeline.search_sessions,
        "update_session_result_summary",
        lambda *_args, **_kwargs: events.append("clarification_write") or {},
    )

    def _forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("clarification must stop the pipeline")

    monkeypatch.setattr(
        pipeline.targeted_search_runtime,
        "execute_local_search",
        _forbidden,
    )
    monkeypatch.setattr(pipeline, "advance_search_session_items", _forbidden)
    monkeypatch.setattr(pipeline, "discover_new_creators", _forbidden)

    result = asyncio.run(
        pipeline.execute_smart_search_profile_advance_pipeline(
            session_id=19,
            payload={"query_text": "unknown prototype lens"},
        )
    )

    assert events == ["clarification_write"]
    assert result["status"] == "needs_clarification"
    assert result["provider_calls_performed"] is False
    assert result["advance"]["status"] == "not_started"
