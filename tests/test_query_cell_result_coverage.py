"""Per-cell acceptance and gap scheduling are bounded, not plan-size claims."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from app.domains.kol import profile_discovery_targeted_batch as bridge
from app.domains.kol import query_cell_result_coverage as coverage
from app.domains.kol import targeted_search_contract as contract


def _cells(count=2, raw_limit=12):
    template = contract.build_query_cells(query="street photographers", body={}, product=None,
        product_focus=(), platforms=["youtube"])[0]
    return [{**deepcopy(template), "query_cell_id": f"cell-{index}",
             "primary_query": f"street photographer district {index}", "raw_limit": raw_limit,
             "fallback_queries": [f"street photographer workshop {index}", f"street photographer walk {index}"]}
            for index in range(count)]


def _retrieve(ledger, cell_id, *, keys=("youtube:alice",), returned=None, round_no=1):
    coverage.record_cell_retrieval(ledger,
        runs=[{"query_cell_id": cell_id, "provider_calls": 1, "round_no": round_no,
               "returned": len(keys) if returned is None else returned}],
        candidates=[{"canonical_key": key, "query_cell_ids": [cell_id]} for key in keys])


def _observation(cell_id, *, key="youtube:alice", status="selected", passed=True, gates=True):
    return {"canonical_key": key, "status": status, "eight_gates_passed": gates,
            "cells": [{"query_cell_id": cell_id, "passed": passed, "reasons": []}]}


def _observe(ledger, observations, *, round_no=1, accepted=(), provider_stop_reason=""):
    coverage.observe_cell_qualification(ledger, {"round_no": round_no, "observations": observations,
        "accepted": list(accepted), "provider_stop_reason": provider_stop_reason})


def test_actual_gate_results_not_matched_tags_determine_cell_counts():
    cells = _cells()
    ledger = coverage.new_cell_result_ledger(cells)
    _retrieve(ledger, "cell-0", keys=("youtube:alice", "youtube:bob"), returned=3)
    _retrieve(ledger, "cell-1")
    observations = [_observation("cell-0"),
        _observation("cell-0", key="youtube:bob", status="pending", gates=False)]
    _observe(ledger, observations, accepted=[observations[0]])
    first = coverage.cell_result_counts(ledger["cells"]["cell-0"])
    assert first["retrieved_count"] == 3
    assert first["unique_count"] == 2 and first["duplicate_count"] == 1
    assert first["qualified_count"] == first["pending_count"] == first["selected_count"] == 1
    second = coverage.cell_result_counts(ledger["cells"]["cell-1"])
    assert second["qualified_count"] is None and second["pending_count"] is None
    assert second["selected_count"] == 0 and second["unassessed_count"] == 1


def test_repeat_creator_new_round_updates_evidence_without_double_unique_or_selected():
    ledger = coverage.new_cell_result_ledger(_cells(1))
    _retrieve(ledger, "cell-0")
    _observe(ledger, [_observation("cell-0", status="pending", gates=False)])
    _retrieve(ledger, "cell-0", round_no=2)
    passed = _observation("cell-0")
    _observe(ledger, [passed], round_no=2, accepted=[passed])
    _observe(ledger, [passed], round_no=2, accepted=[passed])
    counts = coverage.cell_result_counts(ledger["cells"]["cell-0"])
    assert counts["retrieved_count"] == 2 and counts["unique_count"] == 1
    assert counts["duplicate_count"] == counts["qualified_count"] == counts["selected_count"] == 1
    assert counts["pending_count"] == 0


def test_native_id_upgrade_keeps_one_identity_via_observed_handle_alias():
    ledger = coverage.new_cell_result_ledger(_cells(1))
    old_key, new_key = "youtube:handle:alice", "youtube:id:ucfixturealice"
    _retrieve(ledger, "cell-0", keys=(old_key,))
    _observe(ledger, [_observation("cell-0", key=old_key, status="pending", gates=False)])
    coverage.record_cell_retrieval(ledger,
        runs=[{"query_cell_id": "cell-0", "provider_calls": 1, "round_no": 2, "returned": 1}],
        candidates=[{"canonical_key": new_key, "aliases": [old_key, new_key], "query_cell_ids": ["cell-0"]}])
    passed = _observation("cell-0", key=new_key)
    _observe(ledger, [passed], round_no=2, accepted=[passed])
    counts = coverage.cell_result_counts(ledger["cells"]["cell-0"])
    assert counts["unique_count"] == counts["duplicate_count"] == 1
    assert counts["qualified_count"] == counts["selected_count"] == 1
    assert counts["pending_count"] == 0


@pytest.mark.parametrize("gates,passed", [(False, True), (True, False), ("true", True)])
def test_cell_score_alone_or_global_gate_alone_is_not_qualification(gates, passed):
    ledger = coverage.new_cell_result_ledger(_cells(1))
    _retrieve(ledger, "cell-0")
    observed = _observation("cell-0", passed=passed, gates=gates)
    _observe(ledger, [observed], accepted=[observed])
    result = coverage.cell_result_counts(ledger["cells"]["cell-0"])
    assert result["qualified_count"] == result["selected_count"] == 0


@pytest.mark.parametrize("status", ["rejected", "duplicate_local", "duplicate_online"])
def test_later_gate_rejection_or_inventory_duplicate_cannot_become_net_new_coverage(status):
    ledger = coverage.new_cell_result_ledger(_cells(1))
    _retrieve(ledger, "cell-0")
    observation = _observation("cell-0", status=status)
    _observe(ledger, [observation], accepted=[observation])
    counts = coverage.cell_result_counts(ledger["cells"]["cell-0"])
    assert counts["qualified_count"] == counts["selected_count"] == 0
    assert counts["rejected_count"] == 1
    stats = coverage.postgate_qualification_stats({"qualified_cell_count": 1}, [observation])
    assert stats["intent_qualified_cell_count"] == 1
    assert stats["qualified_cell_count"] == stats["candidate_with_qualified_cell_count"] == 0


@pytest.mark.parametrize("reason", ["provider_outcome_unknown", "provider_dispatch_blocked"])
def test_unknown_provider_outcome_remains_pending_even_when_static_gates_pass(reason):
    ledger = coverage.new_cell_result_ledger(_cells(1))
    _retrieve(ledger, "cell-0")
    passed = _observation("cell-0")
    _observe(ledger, [passed], accepted=[passed], provider_stop_reason=reason)
    counts = coverage.cell_result_counts(ledger["cells"]["cell-0"])
    assert counts["qualified_count"] == counts["selected_count"] == 0
    assert counts["pending_count"] == 1


def test_missing_canonical_identity_does_not_fabricate_duplicate_count():
    ledger = coverage.new_cell_result_ledger(_cells(1))
    _retrieve(ledger, "cell-0", keys=("pool:0", "youtube:alice"))
    counts = coverage.cell_result_counts(ledger["cells"]["cell-0"])
    assert counts["unique_count"] is None and counts["duplicate_count"] is None
    assert counts["known_identity_count"] == counts["unidentified_count"] == 1


def test_forecast_or_aggregate_cost_never_becomes_per_cell_actual_cost():
    cells = _cells(1)
    ledger = coverage.new_cell_result_ledger(cells)
    _retrieve(ledger, "cell-0")
    _observe(ledger, [_observation("cell-0")])
    summary = coverage.summarize_cell_results(cells, ledger=ledger,
        runs=[{"query_cell_id": "cell-0", "provider_calls": 1, "returned": 1,
               "executed_query": cells[0]["primary_query"], "cost_usd": 10, "estimated_usd": 20}])
    row = summary["cells"][0]
    assert row["actual_cost_usd"] is None
    assert row["cost_attribution"] == "unavailable_no_cell_scoped_settled_observation"
    assert summary["aggregate_cost_allocation_performed"] is False
    assert row["qualified_count"] == 1 and row["result_coverage_status"] == "covered"
    assert row["coverage_target_source"] == "service_minimum_per_cell"
    assert row["user_requested_per_cell_quota"] is False


def test_qualified_unrelated_cell_id_cannot_supply_coverage():
    ledger = coverage.new_cell_result_ledger(_cells())
    _retrieve(ledger, "cell-0")
    _observe(ledger, [_observation("cell-1")])
    assert coverage.cell_result_counts(ledger["cells"]["cell-1"])["qualified_count"] is None


def test_empty_observed_provider_round_has_known_zero_qualified_not_fake_coverage():
    cells = _cells(1)
    ledger = coverage.new_cell_result_ledger(cells)
    _retrieve(ledger, "cell-0", keys=())
    _observe(ledger, [])
    counts = coverage.cell_result_counts(ledger["cells"]["cell-0"])
    assert counts["qualified_count"] == counts["unique_count"] == counts["duplicate_count"] == 0
    assert coverage.summarize_cell_results(cells, runs=[], ledger=ledger)["status"] == "partial"


def test_second_round_prioritizes_actual_qualification_gap_not_total_success():
    cells = _cells(3)
    ledger = coverage.new_cell_result_ledger(cells)
    _retrieve(ledger, "cell-0")
    _observe(ledger, [_observation("cell-0")])
    selected, diagnostic = coverage.choose_cell_round(cells, ledger=ledger,
        round_no=2, candidate_limit=15, accepted_count=25)
    assert [cell["query_cell_id"] for cell in selected] == ["cell-1"]
    assert diagnostic["deferred_cell_ids"] == ["cell-0", "cell-2"]
    assert diagnostic["reason"] == "minimum_coverage_gaps"
    assert diagnostic["coverage_target"] == 1


def test_all_cells_minimally_covered_can_still_fill_global_shortfall():
    cells = _cells(1)
    ledger = coverage.new_cell_result_ledger(cells)
    _retrieve(ledger, "cell-0")
    _observe(ledger, [_observation("cell-0")])
    selected, diagnostic = coverage.choose_cell_round(cells, ledger=ledger,
        round_no=2, candidate_limit=20, accepted_count=1)
    assert selected and diagnostic["reason"] == "global_shortfall_after_minimum_coverage"


@pytest.mark.parametrize("round_no,limit,accepted,expected", [
    (1, 150, 0, 8), (2, 29, 0, 2), (3, 9, 0, 0), (4, 150, 0, 0), (2, 150, 30, 0),
])
def test_service_caps_remain_eight_cells_three_rounds_fifteen_each(round_no, limit, accepted, expected):
    cells = _cells(12, raw_limit=15)
    selected, diagnostic = coverage.choose_cell_round(cells,
        ledger=coverage.new_cell_result_ledger(cells), round_no=round_no,
        candidate_limit=limit, accepted_count=accepted)
    assert len(selected) == expected
    assert sum(cell["raw_limit"] for cell in selected) <= limit
    assert all(10 <= cell["raw_limit"] <= 15 for cell in selected)
    assert diagnostic["raw_limit_total"] == sum(cell["raw_limit"] for cell in selected)


def _offline_bridge(monkeypatch):
    monkeypatch.setattr(bridge.profile_discovery_rounds, "round_cost_forecast", lambda *_args, **_kwargs: {
        "platforms": ["youtube"], "estimated_usd": 0.0})
    monkeypatch.setattr(bridge.profile_discovery_rounds, "forecast_line", lambda _row: "offline")
    monkeypatch.setattr(bridge.profile_discovery_rounds, "online_deadline_seconds", lambda: 60)
    monkeypatch.setattr(bridge.profile_discovery_evidence, "observe_round", lambda **_kwargs: {})
    monkeypatch.setattr(bridge.recall_favorite_exclusion, "exclude_favorited_online_candidates", lambda rows, **_kwargs: (rows, {}))


def test_real_bridge_never_reserves_more_than_150_raw_rows_across_fallbacks(monkeypatch):
    _offline_bridge(monkeypatch)
    cells, state, calls = _cells(8, raw_limit=15), {}, []

    async def discover(**kwargs):
        calls.append(kwargs)
        return {"status": "ready", "provider_calls": True, "platforms": ["youtube"],
                "new_creators": [{"platform": "youtube", "handle": "fixture-artist"}]}

    for round_no in (1, 2, 3):
        asyncio.run(bridge.fetch_targeted_round(round_no=round_no, query_cells=cells,
            discovery_kwargs={"platforms": ["youtube"]}, plan_legs=["youtube"], state=state,
            favorite_identity_keys=set(), discover=discover, candidate_limit=150))
    assert len(calls) == 10
    assert sum(call["limit"] for call in calls) == 150
    assert state["targeted_gate_stopped_by"] == "candidate_budget_exhausted"
    assert sum(state["targeted_candidate_reservations"].values()) == 150
    result = bridge.finalize_online_result({"items": [], "shortfall": 30}, query_cells=cells,
        query_cells_omitted=0, search_brief={}, objective="prospective_growth", state=state)
    assert result["query_cell_coverage"]["status"] == "partial"
    assert all(row["qualified_count"] is None for row in result["query_cell_coverage"]["cells"])


def test_cached_gate_selection_cannot_ignore_smaller_fetch_limit(monkeypatch):
    _offline_bridge(monkeypatch)
    cells, state, calls = _cells(), {}, []
    gate = bridge.build_targeted_round_gate(query_cells=cells,
        discovery_kwargs={"platforms": ["youtube"]}, plan_legs=["youtube"], state=state)
    assert gate(1)["allowed"] is True

    async def discover(**kwargs):
        calls.append(kwargs)
        return {}

    result = asyncio.run(bridge.fetch_targeted_round(round_no=1, query_cells=cells,
        discovery_kwargs={"platforms": ["youtube"]}, plan_legs=["youtube"], state=state,
        favorite_identity_keys=set(), discover=discover, candidate_limit=10))
    assert calls == [] and result["provider_calls"] is False
    assert state["targeted_gate_stopped_by"] == "candidate_budget_changed_after_preflight"


@pytest.mark.parametrize("stale", [False, True])
def test_real_execution_collector_observer_bridge_keeps_each_cell_qualification_separate(monkeypatch, stale):
    from app.domains.kol import profile_online_qualification as online
    from app.services.intelligence.account_search_provider_policy import build_provider_discovery_policy

    _offline_bridge(monkeypatch)
    cells = contract.build_query_cells(query="street photographers and portrait photographers",
        body={}, product=None, product_focus=(), platforms=["youtube"])
    assert len(cells) == 2
    state, enrolled = {}, []
    as_of = datetime(2026, 8, 17, tzinfo=timezone.utc)
    raw = {
        "platform": "youtube", "handle": "streetfixture", "channel_id": "UCstreetfixture01",
        "profile_url": "https://www.youtube.com/@streetfixture", "followers": 5000,
        "country": "US", "country_source": "platform_profile", "language": "en",
        "language_source": "platform_profile", "profile_type": "creator",
        "profile_type_source": "provider_declared", "activation_sample_count": 5,
        "activation_metrics_source": "fixture.recent_video_aggregate",
        "activation_metrics_scope": "recent_video_aggregate_45d", "avg_views": 20000,
        "avg_comments": 180, "engagement_rate": 0.08,
        "bio": "street photographer street photography creator",
        "latest_real_video": {"posted_at": (as_of - timedelta(days=100 if stale else 16)).isoformat(), "video_id": "streetvideo01",
            "platform": "youtube", "title": "street photography documentary",
            "description": "street photographer walks and photographs streets",
            "source": "platform_video_api"},
    }

    async def discover(**_kwargs):
        return {"status": "ready", "provider_calls": True, "platforms": ["youtube"],
                "new_creators": [deepcopy(raw)]}

    async def fetch_batch(*, round_no, limit, cursor):
        return await bridge.fetch_targeted_round(round_no=round_no, query_cells=cells,
            discovery_kwargs={"platforms": ["youtube"]}, plan_legs=["youtube"], state=state,
            favorite_identity_keys=set(), discover=discover, candidate_limit=limit)

    def enroll(candidate):
        enrolled.append(candidate)
        return {"kol_pool_id": 901}

    result = asyncio.run(online.collect_strict_online_candidates(
        query_text="street photographers and portrait photographers",
        policy=online.online_policy(market="US", platforms=["youtube"], languages=["en"], profile_types=["creator"],
            gate_mode="relaxed", **({"provider_discovery_policy": build_provider_discovery_policy(recent_activity_max_age_days=365)} if stale else {})),
        local_canonical_keys=set(), fetch_batch=fetch_batch, enroll_candidate=enroll,
        max_provider_rounds=1, search_brief={"objective": "prospective_growth"},
        round_observer=bridge.build_query_cell_round_observer(query_cells=cells, state=state),
        as_of=as_of,
    ))
    result = bridge.finalize_online_result(result, query_cells=cells, query_cells_omitted=0,
        search_brief={}, objective="prospective_growth", state=state)
    assert result["returned_count"] == len(enrolled) == (0 if stale else 1)
    rows = result["query_cell_coverage"]["cells"]
    assert [row["unique_count"] for row in rows] == [1, 1]
    assert sorted(row["qualified_count"] for row in rows) == ([0, 0] if stale else [0, 1])
    assert sorted(row["selected_count"] for row in rows) == ([0, 0] if stale else [0, 1])
    if stale:
        assert all(row["rejected_count"] == 1 for row in rows)
        assert result["rejected_by_reason"]["discovery_content_outside_window"] == 1
    assert result["query_cell_coverage"]["status"] == "partial"
    assert state["targeted_accepted_count"] == (0 if stale else 1)
