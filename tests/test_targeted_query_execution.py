from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.domains.kol import profile_discovery_targeted_batch, targeted_query_execution
from app.domains.kol.targeted_search_contract import build_locked_term_groups


def _cell(cell_id: str, segment: str) -> dict[str, Any]:
    return {
        "query_cell_id": cell_id,
        "objective": "prospective_growth",
        "segment": segment,
        "segment_label": segment,
        "primary_query": f"{segment} photographer on-camera flash",
        "platforms": ["youtube"],
        "round": 1,
        "raw_limit": 12,
        "required_evidence_groups": [
            "product_use_fit",
            "segment_use_case",
            "market_activation",
        ],
        "brand_or_model_required": False,
        "brand_or_model_ranking_weight": 0,
        "locked_term_groups": build_locked_term_groups(
            capability="on-camera flash",
            segment=segment,
        ),
    }


def test_multi_cell_execution_dedupes_candidates_but_retains_every_cell_match() -> None:
    calls: list[str] = []

    async def discover(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs["query_text"])
        return {
            "status": "ready",
            "platforms": ["youtube"],
            "new_creators": [{
                "platform": "youtube",
                "handle": "shared-creator",
                "channel_id": "UCshared123",
                "profile_url": "https://www.youtube.com/@shared-creator",
            }],
            "platform_results": [{"platform": "youtube", "status": "ready"}],
            "provider_calls": True,
        }

    cells = [_cell("cell-motorsport", "motorsport"), _cell("cell-food", "food")]
    result = asyncio.run(targeted_query_execution.execute_first_round_query_cells(
        query_cells=cells,
        base_kwargs={"platforms": ["youtube"]},
        discover=discover,
    ))

    assert calls == [cell["primary_query"] for cell in cells]
    assert result["raw_candidate_occurrences"] == 2
    assert result["unique_candidate_count"] == 1
    assert result["candidate_cell_match_count"] == 2
    assert len(result["new_creators"]) == 1
    assert [
        cell["query_cell_id"]
        for cell in result["new_creators"][0]["matched_query_cells"]
    ] == ["cell-motorsport", "cell-food"]


def test_query_cells_input_must_be_a_list() -> None:
    cells = (_cell("cell-food", "food"),)

    normalized, omitted = targeted_query_execution.normalize_first_round_cells(cells)

    assert normalized == []
    assert omitted == 0


def test_discovery_cancellation_propagates_to_caller() -> None:
    async def cancelled(**_kwargs: Any) -> dict[str, Any]:
        raise asyncio.CancelledError

    async def run() -> None:
        await targeted_query_execution.execute_first_round_query_cells(
            query_cells=[_cell("cell-food", "food")],
            base_kwargs={"platforms": ["youtube"]},
            discover=cancelled,
        )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run())


def test_execution_projection_ignores_client_locked_terms_and_unknown_aliases() -> None:
    cell = _cell("cell-motorsport", "motorsport")
    cell["locked_terms"] = {"product_use_fit": ["zoomlight"]}
    cell["locked_term_groups"]["groups"][0]["aliases"].append("zoomlight")

    normalized, _ = targeted_query_execution.normalize_first_round_cells([cell])

    assert "locked_terms" not in normalized[0]
    product = normalized[0]["locked_term_groups"]["groups"][0]
    assert product["canonical_term"] == "on-camera flash"
    assert "zoomlight" not in product["aliases"]


def test_unspecified_operator_platforms_do_not_inherit_planner_cell_restriction() -> None:
    seen: list[dict[str, Any]] = []

    async def discover(**kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs)
        return {
            "status": "ready",
            "platforms": ["youtube", "instagram", "tiktok"],
            "new_creators": [],
            "platform_results": [],
            "provider_calls": True,
        }

    asyncio.run(targeted_query_execution.execute_first_round_query_cells(
        query_cells=[_cell("cell-food", "food")],
        # Empty is an authoritative "not restricted" projection from the
        # worker, not permission to reuse the planner's YouTube-only hint.
        base_kwargs={"platforms": []},
        discover=discover,
    ))

    assert len(seen) == 1
    assert seen[0]["platforms"] == []


def test_explicit_operator_platforms_override_planner_cell_platforms() -> None:
    seen: list[dict[str, Any]] = []

    async def discover(**kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs)
        return {
            "status": "ready",
            "platforms": ["instagram"],
            "new_creators": [],
            "platform_results": [],
            "provider_calls": True,
        }

    asyncio.run(targeted_query_execution.execute_first_round_query_cells(
        query_cells=[_cell("cell-food", "food")],
        base_kwargs={"platforms": ["instagram"]},
        discover=discover,
    ))

    assert len(seen) == 1
    assert seen[0]["platforms"] == ["instagram"]


def test_shortfall_rounds_use_each_server_fallback_once_and_keep_lineage() -> None:
    cell = _cell("cell-food", "food")
    cell["fallback_queries"] = [
        "food photographer tutorial",
        "food photographer gear",
        # Duplicate and primary aliases are normalized out before execution.
        "food photographer gear",
    ]
    calls: list[dict[str, Any]] = []

    async def discover(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "status": "ready",
            "platforms": ["youtube"],
            "new_creators": [{
                "platform": "youtube",
                "handle": f"creator-{len(calls)}",
                "channel_id": f"UC{len(calls):022d}",
            }],
            "platform_results": [{"platform": "youtube", "status": "ready"}],
            "provider_calls": True,
        }

    first = asyncio.run(targeted_query_execution.execute_query_cell_round(
        query_cells=[cell],
        base_kwargs={"platforms": ["youtube"]},
        discover=discover,
        round_no=1,
    ))
    second = asyncio.run(targeted_query_execution.execute_query_cell_round(
        query_cells=[cell],
        base_kwargs={"platforms": ["youtube"]},
        discover=discover,
        round_no=2,
    ))
    third = asyncio.run(targeted_query_execution.execute_query_cell_round(
        query_cells=[cell],
        base_kwargs={"platforms": ["youtube"]},
        discover=discover,
        round_no=3,
    ))
    exhausted = asyncio.run(targeted_query_execution.execute_query_cell_round(
        query_cells=[cell],
        base_kwargs={"platforms": ["youtube"]},
        discover=discover,
        round_no=4,
    ))

    assert [call["query_text"] for call in calls] == [
        cell["primary_query"],
        "food photographer tutorial",
        "food photographer gear",
    ]
    assert all(call["exact_query"] is True for call in calls)
    assert all(call["page_cursors"] is None for call in calls)
    assert first["has_more"] is True
    assert first["next_cursor"]["next_round"] == 2
    assert second["has_more"] is True
    assert second["fallback_queries_used"] is True
    lineage = second["new_creators"][0]["targeted_search"]
    assert lineage["query_cell_id"] == "cell-food"
    assert lineage["primary_query"] == cell["primary_query"]
    assert lineage["executed_query"] == "food photographer tutorial"
    assert lineage["round_no"] == 2
    assert lineage["query_variant"] == "fallback"
    assert lineage["fallback_index"] == 0
    assert third["has_more"] is False
    assert exhausted["status"] == "exhausted"
    assert exhausted["provider_calls"] is False


def test_targeted_first_round_cost_gate_blocks_before_any_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def discover(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        raise AssertionError("budget denial must happen before provider IO")

    monkeypatch.setattr(
        profile_discovery_targeted_batch.profile_discovery_rounds,
        "daily_discovery_spend_usd",
        lambda: {"available": True, "spend_usd": 4.5, "run_count": 9},
    )
    state: dict[str, Any] = {
        "round_forecasts": [],
        "term_rounds": [],
        "observed_candidates": [],
        "favorite_blocks": [],
        "round_legs": [],
        "round_cursor": {},
        "round_yield": {"last": 0},
    }
    result = asyncio.run(profile_discovery_targeted_batch.fetch_targeted_round(
        round_no=1,
        query_cells=[_cell("cell-food", "food"), _cell("cell-sport", "sport")],
        discovery_kwargs={"platforms": ["instagram"]},
        plan_legs=["instagram"],
        state=state,
        favorite_identity_keys=set(),
        discover=discover,
    ))

    assert calls == []
    assert result["status"] == "blocked"
    assert result["provider_calls"] is False
    assert result["provider_gate"]["reason"] == "daily_budget_exhausted"
    assert result["provider_gate"]["forecast"]["query_cell_count"] == 2
    assert result["provider_gate"]["forecast"]["estimated_usd"] > 0.5
    assert len(state["round_forecasts"]) == 2
    assert all(row["gate_allowed"] is False for row in state["round_forecasts"])
    finalized = profile_discovery_targeted_batch.finalize_online_result(
        {
            "provider_rounds": 1,
            "exhausted": False,
            "shortfall": 30,
            "shortfall_reasons": {"daily_budget_exhausted": 30},
            "round_gate": {"stopped_by": "daily_budget_exhausted", "verdicts": []},
        },
        query_cells=[_cell("cell-food", "food")],
        query_cells_omitted=0,
        search_brief={"search_spec_version": "test"},
        objective="prospective_growth",
        state=state,
    )
    assert finalized["targeted_search"]["provider_rounds"] == 0
    assert finalized["targeted_search"]["collector_rounds"] == 1
    assert finalized["targeted_search"]["has_more"] is True


def test_targeted_youtube_forecast_keeps_all_three_usage_dimensions_separate() -> None:
    """Two exact cells mean two independent search+metadata call sets."""

    plan = targeted_query_execution.plan_query_cell_round(
        query_cells=[_cell("cell-food", "food"), _cell("cell-sport", "sport")],
        base_kwargs={"platforms": ["youtube"]},
        round_no=1,
    )
    state: dict[str, Any] = {"round_forecasts": []}

    verdict = profile_discovery_targeted_batch.preflight_targeted_round(
        plan,
        plan_legs=["youtube"],
        state=state,
    )

    assert verdict["allowed"] is True
    assert verdict["forecast"] == {
        "round_no": 1,
        "query_cell_count": 2,
        "platforms": ["youtube"],
        "apify_runs": 0,
        "youtube_search_calls": 2,
        "youtube_combined_quota_units": 4,
        "youtube_api_calls": 6,
        "youtube_quota_units": 4,
        "youtube_quota_units_deprecated": True,
        "estimated_usd": 0.0,
    }
    assert len(state["round_forecasts"]) == 2
    assert all(row["youtube_search_calls"] == 1 for row in state["round_forecasts"])
    assert all(row["youtube_combined_quota_units"] == 2 for row in state["round_forecasts"])
    assert all(row["youtube_api_calls"] == 3 for row in state["round_forecasts"])
    assert all(row["youtube_quota_units"] == 2 for row in state["round_forecasts"])
    assert all(row["youtube_quota_units_deprecated"] is True for row in state["round_forecasts"])
    assert profile_discovery_targeted_batch.term_evidence_youtube_forecast_kwargs(
        state["round_forecasts"]
    ) == {
        "youtube_search_calls_forecast": 2,
        "youtube_combined_quota_units_forecast": 4,
        "youtube_api_calls_forecast": 6,
    }
    assert profile_discovery_targeted_batch.term_evidence_youtube_forecast_kwargs([]) == {
        "youtube_search_calls_forecast": None,
        "youtube_combined_quota_units_forecast": None,
        "youtube_api_calls_forecast": None,
    }


def test_targeted_round_actuals_map_to_round_plan_without_legacy_quota_sum() -> None:
    kwargs = profile_discovery_targeted_batch.round_plan_actual_youtube_kwargs([
        {
            "youtube_search_calls_actual": 1,
            "youtube_combined_quota_units_actual": 2,
            "youtube_api_calls_actual": 3,
            "quota_units_actual": 999,
        },
        {
            "youtube_search_calls_actual": 2,
            "youtube_combined_quota_units_actual": 2,
            "youtube_api_calls_actual": 4,
            "quota_units_actual": 999,
        },
    ])

    assert kwargs == {
        "actual_search_calls": 3,
        "actual_combined_quota_units": 4,
        "actual_youtube_api_calls": 7,
    }
    assert "actual_quota_units" not in kwargs


def test_fallback_round_gate_preflights_once_then_executes_exact_cell_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cell = _cell("cell-food", "food")
    cell["fallback_queries"] = ["food photographer tutorial"]
    calls: list[dict[str, Any]] = []
    spend_reads = 0

    async def discover(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "status": "empty",
            "platforms": ["instagram"],
            "new_creators": [],
            "platform_results": [{"platform": "instagram", "status": "empty"}],
            "provider_calls": True,
        }

    def read_spend() -> dict[str, Any]:
        nonlocal spend_reads
        spend_reads += 1
        return {"available": True, "spend_usd": 0.0, "run_count": 0}

    monkeypatch.setattr(
        profile_discovery_targeted_batch.profile_discovery_rounds,
        "daily_discovery_spend_usd",
        read_spend,
    )
    state: dict[str, Any] = {
        "round_forecasts": [],
        "term_rounds": [],
        "observed_candidates": [],
        "favorite_blocks": [],
        "round_legs": [],
        "round_cursor": {},
        "round_yield": {"last": 0},
    }
    discovery_kwargs = {"platforms": ["instagram"]}
    gate = profile_discovery_targeted_batch.build_targeted_round_gate(
        query_cells=[cell],
        discovery_kwargs=discovery_kwargs,
        plan_legs=["instagram"],
        state=state,
    )

    verdict = gate(2)
    assert verdict["allowed"] is True
    assert calls == []
    assert len(state["round_forecasts"]) == 1
    result = asyncio.run(profile_discovery_targeted_batch.fetch_targeted_round(
        round_no=2,
        query_cells=[cell],
        discovery_kwargs=discovery_kwargs,
        plan_legs=["instagram"],
        state=state,
        favorite_identity_keys=set(),
        discover=discover,
    ))

    assert spend_reads == 1
    assert len(state["round_forecasts"]) == 1
    assert [call["query_text"] for call in calls] == ["food photographer tutorial"]
    assert calls[0]["exact_query"] is True
    assert result["fallback_queries_used"] is True
    assert state["fallback_queries_used"] is True
    assert state["targeted_rounds_executed"] == [2]
