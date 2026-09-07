"""Actual outbound QueryCell intent is distinct from creator qualification."""
from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from app.domains.kol import profile_discovery_targeted_batch as bridge
from app.domains.kol import search_plan_semantics as semantics
from app.domains.kol import targeted_query_execution as execution
from app.domains.kol import targeted_search_contract as contract


def _cells(query="street photographers POV", *, focus=()):
    return contract.build_query_cells(query=query, body={}, product=None,
                                     product_focus=focus, platforms=["youtube"])


def _run(cells, *, round_no=1, calls=None):
    captured = calls if calls is not None else []

    async def discover(**kwargs):
        captured.append(kwargs)
        return {"status": "ready", "provider_calls": True, "platforms": ["youtube"],
                "new_creators": [{"platform": "youtube", "handle": "fixture-artist"}]}

    result = asyncio.run(execution.execute_query_cell_round(
        query_cells=cells, base_kwargs={"platforms": ["youtube"], "query_text": "ignored broad keyword"},
        discover=discover, round_no=round_no,
    ))
    return result, captured


@pytest.mark.parametrize("query,angle", [
    ("street photographers POV", "pov"), ("街拍摄影师第一视角", "pov"),
    ("street photographers tutorial", "instruction"), ("街拍摄影师拍摄花絮", "behind_scenes"),
])
def test_bound_operator_format_is_preserved_through_real_builder_normalizer_and_execution(query, angle):
    cells = _cells(query)
    assert len(cells) == 1
    before = deepcopy(cells)
    normalized, omitted = execution.normalize_first_round_cells(cells)
    assert omitted == 0 and cells == before
    assert normalized[0]["query_intent"]["angle"]["terms"] == [angle]
    assert normalized[0]["query_intent"]["angle"]["source"] == "operator_text"
    result, calls = _run(normalized)
    assert len(calls) == 1
    assert calls[0]["query_text"] == calls[0]["search_query_en"] == normalized[0]["primary_query"]
    assert calls[0]["auto_enroll"] is False and calls[0]["exact_query"] is True
    assert result["query_cell_runs"][0]["query_intent_validation"]["status"] == "covered"
    matched = result["new_creators"][0]["matched_query_cells"][0]
    assert matched["query_intent"]["angle"]["terms"] == [angle]
    assert matched["query_intent_validation"]["candidate_qualification"] == "not_evaluated"
    assert matched["query_intent_validation"]["target_evidence_status"] == "not_evaluated"


def test_no_requested_scene_or_angle_is_not_filled_with_generic_terms():
    cells = _cells("find photographers")
    assert len(cells) == 1
    intent = cells[0]["query_intent"]
    assert intent["scene"]["terms"] == [] and intent["scene"]["status"] == "not_requested"
    assert intent["angle"]["terms"] == [] and intent["angle"]["status"] == "not_requested"
    assert intent["target_evidence"]
    assert all(item["status"] == "not_evaluated" for item in intent["target_evidence"])


def test_negated_format_does_not_become_positive_operator_requirement():
    assert semantics.operator_query_angles("不要POV，找街拍摄影师") == []
    cell = _cells("不要POV，找街拍摄影师")[0]
    assert cell["query_intent"]["angle"]["terms"] == []
    assert "POV" not in cell["primary_query"]


def test_unresolved_format_to_multiple_cells_is_partial_not_guessed():
    cells = _cells("wedding filmmakers behind the scenes and street photographers POV")
    assert len(cells) >= 2
    for cell in cells:
        assert cell["query_intent"]["angle"]["scope"] == "unresolved"
        verdict = semantics.validate_query_cell_execution(cell, cell["primary_query"])
        assert verdict["angle"]["status"] == "unresolved"
        assert verdict["status"] == "partial"
        assert verdict["execution_allowed"] is True


@pytest.mark.parametrize("query", ["gear review", "street photographer", "not street photographer POV"])
def test_actual_outbound_query_missing_bound_operator_terms_is_blocked(query):
    bad = _cells()[0]
    bad["primary_query"] = query
    bad["query_intent_validation"] = {"status": "covered", "execution_allowed": True}
    bad["coverage_status"] = "covered"
    good = _cells("portrait photographers")[0]
    result, calls = _run([bad, good])
    assert len(calls) == 1 and calls[0]["query_text"] == good["primary_query"]
    assert len(result["query_cell_runs"]) == 2
    failed, accepted = result["query_cell_runs"]
    assert failed["status"] == "blocked" and failed["provider_calls"] == 0
    assert failed["query_intent_validation"]["status"] == "invalid"
    assert accepted["provider_calls"] == 1
    assert result["query_cell_coverage"]["status"] == "partial"
    assert result["query_cell_coverage"]["query_cells_executed"] == 1


def test_later_fallback_is_checked_at_its_actual_execution_not_primary_validation():
    cell = _cells()[0]
    assert semantics.validate_query_cell_execution(cell, cell["primary_query"])["status"] == "covered"
    cell["fallback_queries"] = ["generic gear review"]
    result, calls = _run([cell], round_no=2)
    assert calls == []
    assert result["query_cell_runs"][0]["executed_query"] == "generic gear review"
    assert result["query_cell_runs"][0]["query_intent_validation"]["execution_allowed"] is False


@pytest.mark.parametrize("field,value", [
    ("schema", "forged"), ("angle", None), ("scene", "street"),
    ("target_evidence", []), ("angle", {"terms": ["pov"], "source": [], "mode": "all", "scope": "cell"}),
])
def test_malformed_new_contract_is_not_downgraded_to_legacy(field, value):
    cell = _cells()[0]
    cell["query_intent"][field] = value
    normalized, _ = execution.normalize_first_round_cells([cell])
    assert len(normalized) == 1
    assert normalized[0]["query_intent"]["contract_status"] == "invalid"
    result, calls = _run(normalized)
    assert calls == []
    assert result["query_cell_runs"][0]["coverage_status"] == "invalid"


def test_model_scene_is_descriptive_and_does_not_become_an_operator_lock():
    cell = _cells("", focus=["wildlife photographer"])[0]
    intent = cell["query_intent"]
    assert intent["scene"]["source"] == "planner_inferred"
    assert intent["scene"]["required_in_query"] is False
    verdict = semantics.validate_query_cell_execution(cell, "portrait photographer")
    assert verdict["execution_allowed"] is True
    assert verdict["status"] == "partial"
    assert verdict["candidate_qualification"] == "not_evaluated"


def test_legacy_cell_keeps_execution_compatibility_but_never_claims_covered():
    cell = _cells()[0]
    cell.pop("query_intent")
    result, calls = _run([cell])
    assert len(calls) == 1
    assert result["query_cell_runs"][0]["coverage_status"] == "legacy_unverified"
    assert result["query_cell_coverage"]["cells"][0]["coverage_status"] == "legacy_unverified"
    assert result["query_cell_coverage"]["status"] == "partial"


def test_duplicate_new_cells_are_preserved_but_do_not_spend_twice_or_share_evidence():
    first = _cells()[0]
    second = deepcopy(first)
    second["query_cell_id"] = "separate-requested-cell"
    normalized, _ = execution.normalize_first_round_cells([first, second])
    assert len(normalized) == 2
    result, calls = _run(normalized)
    assert len(calls) == 1
    assert len(result["query_cell_runs"]) == 2
    assert result["query_cells_executed"] == 1
    assert result["query_cell_runs"][1]["status"] == "not_executed_duplicate_query"
    assert result["query_cell_runs"][1]["coverage_status"] == "partial"
    rows = result["query_cell_coverage"]["cells"]
    assert [row["coverage_status"] for row in rows] == ["covered", "not_executed_duplicate_query"]
    assert [item["query_cell_id"] for item in result["new_creators"][0]["matched_query_cells"]] == [first["query_cell_id"]]
    assert result["query_cell_coverage"]["status"] == "partial"


def test_distinct_actual_search_operators_are_not_heuristically_deduplicated():
    first = _cells()[0]
    second = deepcopy(first)
    second["query_cell_id"] = "quoted-phrase-query"
    second["primary_query"] = '"street photographer" POV'
    result, calls = _run([first, second])
    assert len(calls) == 2
    assert result["query_cell_coverage"]["query_cells_executed"] == 2
    assert len(result["new_creators"][0]["matched_query_cells"]) == 2


def test_planned_or_omitted_cells_cannot_count_as_actual_execution():
    cells = _cells()
    unexecuted = semantics.summarize_query_cell_coverage(cells)
    assert unexecuted["query_cells_executed"] == 0
    assert unexecuted["cells"][0]["coverage_status"] == "not_executed"
    batch, _ = _run(cells)
    omitted = semantics.summarize_query_cell_coverage(cells, batch["query_cell_runs"], omitted_count=1)
    assert omitted["query_cells_requested"] == 2
    assert omitted["query_cells_executed"] == 1
    assert omitted["status"] == "partial"


def test_bridge_carries_actual_run_coverage_instead_of_len_cells(monkeypatch):
    cells = _cells()
    duplicate = deepcopy(cells[0])
    duplicate["query_cell_id"] = "unexecuted-duplicate"
    cells.append(duplicate)
    batch, _ = _run(cells)
    batch["targeted_round_complete"] = False  # A collector stop must not erase observations.

    async def execute(**_kwargs):
        return batch

    monkeypatch.setattr(bridge.targeted_query_execution, "execute_query_cell_round", execute)
    state = {}
    asyncio.run(bridge.fetch_targeted_round(round_no=1, query_cells=cells, discovery_kwargs={},
        plan_legs=["youtube"], state=state, favorite_identity_keys=set(), discover=None))
    batch["query_cell_runs"][0]["query_intent"]["angle"]["terms"].clear()
    assert state["query_cell_runs"][0]["query_intent"]["angle"]["terms"] == ["pov"]
    result = bridge.finalize_online_result({"provider_rounds": 1}, query_cells=cells,
        query_cells_omitted=0, search_brief={}, objective="prospective_growth", state=state)
    assert result["targeted_search"]["query_cells_requested"] == 2
    assert result["targeted_search"]["query_cells_executed"] == 1
    assert result["query_cell_coverage"]["status"] == "partial"
    assert result["query_cell_coverage"]["cells"][1]["coverage_status"] == "not_executed_duplicate_query"


def test_normalization_cannot_promote_forged_candidate_qualification():
    cell = _cells()[0]
    cell["query_intent"]["candidate_qualification"] = "qualified"
    cell["query_intent"]["claim_status"] = "measured"
    normalized, _ = execution.normalize_first_round_cells([cell])
    assert normalized[0]["query_intent"]["candidate_qualification"] == "not_evaluated"
    assert normalized[0]["query_intent"]["claim_status"] == "descriptive_only"


def test_client_angle_metadata_cannot_replace_server_rebuilt_operator_contract():
    from app.domains.kol.profile_discovery_queue import _smart_profile_payload

    forged = _cells()[0]
    payload = _smart_profile_payload(
        query="street photographers", body={"query_cells": [forged], "query_intent": forged["query_intent"]},
        staff=None, session_id=1, recall_filters={}, smart_local_30=False,
        smart_online_30=False, triggered_by_user_id=None,
    )
    assert "query_cells" not in payload and "query_intent" not in payload
    rebuilt = contract.build_query_cells(query=payload["query_text"], body=payload,
        product=None, product_focus=(), platforms=["youtube"])
    assert rebuilt[0]["query_intent"]["angle"]["status"] == "not_requested"


def test_plan_reports_unresolved_per_cell_angle_as_review_not_qualification():
    plan = contract.apply_targeted_contract({},
        query="wedding filmmakers behind the scenes and street photographers POV", body={})
    assert plan["query_plan_semantics"]["status"] == "needs_review"
    assert plan["query_plan_semantics"]["candidate_qualification"] == "not_evaluated"
    assert all(row["validation"]["status"] == "partial" for row in plan["query_plan_semantics"]["query_cell_contracts"])


def test_online_safe_projection_keeps_provenance_for_repeat_validation():
    from app.domains.kol.profile_online_evidence import _candidate_query_cells, _safe_query_cell

    result, _ = _run(_cells())
    source = result["new_creators"][0]
    safe = _candidate_query_cells(source, query_text="not-the-executed-query")[0]
    assert safe["segment_source"] == source["targeted_search"]["segment_source"]
    assert safe["segment_locked"] is True
    assert semantics.validate_query_cell_execution(safe, safe["executed_query"])["status"] == "covered"
    repeated = _safe_query_cell(safe)
    assert repeated["query_intent_validation"]["status"] == "covered"
    assert repeated["query_intent"] == safe["query_intent"]


def test_online_projection_never_substitutes_planned_query_for_missing_execution():
    from app.domains.kol.profile_online_evidence import _safe_query_cell

    planned = _cells()[0]
    planned["query_intent_validation"] = {"status": "covered", "execution_allowed": True}
    safe = _safe_query_cell(planned)
    assert "executed_query" not in safe
    assert safe["query_intent_validation"]["status"] == "invalid"
    assert safe["query_intent_validation"]["issues"] == ["executed_query_missing"]


def test_round_gate_and_fetch_share_preflight_without_double_forecast_reservation(monkeypatch):
    from app.domains.kol.profile_discovery_pipeline_online import _EvidenceLedger

    ledger = _EvidenceLedger()
    cells, spend_checks, calls = _cells(), [], []
    monkeypatch.setattr(bridge.profile_discovery_rounds, "round_cost_forecast", lambda *_args, **_kwargs: {
        "platforms": ["instagram"], "estimated_usd": 0.25, "apify_runs": 1,
    })
    monkeypatch.setattr(bridge.profile_discovery_rounds, "forecast_line", lambda _row: "offline fixture")
    monkeypatch.setattr(bridge.profile_discovery_rounds, "daily_budget_usd", lambda: 1.0)
    monkeypatch.setattr(bridge.profile_discovery_rounds, "online_deadline_seconds", lambda: 60)
    monkeypatch.setattr(bridge.profile_discovery_rounds, "daily_discovery_spend_usd", lambda: spend_checks.append(1) or {
        "available": True, "spend_usd": 0.0,
    })
    monkeypatch.setattr(bridge.profile_discovery_evidence, "observe_round", lambda **_kwargs: {})
    monkeypatch.setattr(bridge.recall_favorite_exclusion, "exclude_favorited_online_candidates", lambda rows, **_kwargs: (rows, {}))

    async def discover(**kwargs):
        calls.append(kwargs)
        return {"status": "empty", "provider_calls": True, "new_creators": [], "platforms": ["instagram"]}

    first_state = ledger.targeted_state()
    gate = bridge.build_targeted_round_gate(query_cells=cells, discovery_kwargs={"platforms": ["instagram"]},
        plan_legs=["instagram"], state=first_state)
    verdict = gate(1)
    assert verdict["allowed"] is True
    started = first_state["targeted_started_monotonic"]
    asyncio.run(bridge.fetch_targeted_round(round_no=1, query_cells=cells,
        discovery_kwargs={"platforms": ["instagram"]}, plan_legs=["instagram"],
        state=ledger.targeted_state(), favorite_identity_keys=set(), discover=discover))
    assert ledger.targeted_state() is first_state
    assert len(calls) == len(spend_checks) == len(ledger.round_forecasts) == 1
    assert first_state["targeted_authorized_estimated_usd"] == 0.25
    assert first_state["targeted_started_monotonic"] == started
    assert first_state["targeted_preflights"]["1"] is verdict
    assert first_state["query_cell_run_counters"] is ledger.query_cell_run_counters
    assert len(ledger.query_cell_runs) == 1
