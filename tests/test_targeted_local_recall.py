from __future__ import annotations

from typing import Any

import pytest

from app.domains.kol import (
    profile_discovery_candidates,
    profile_recall,
    profile_recall_qualification,
    targeted_local_recall,
    targeted_search_contract,
    targeted_search_runtime,
)


def _cell(index: int, *, raw_limit: int = 15) -> dict[str, Any]:
    return {
        "query_cell_id": f"cell-{index}",
        "objective": "existing_evidence",
        "segment": f"segment-{index}",
        "segment_label": f"Segment {index}",
        "primary_query": f"segment {index} flash creator",
        "platforms": ["youtube"],
        "round": 1,
        "raw_limit": raw_limit,
        "required_evidence_groups": ["product_use_fit", "segment_use_case"],
        "brand_or_model_required": True,
    }


def _item(
    item_id: int,
    *,
    passed: bool,
    deferred: bool = False,
    score: float = 90.0,
) -> dict[str, Any]:
    return {
        "kol_pool_id": item_id,
        "platform": "youtube",
        "handle": f"creator{item_id}",
        "bucket": "creator",
        "display_rank_score": score,
        "qualification_evidence": {
            "passed": passed,
            "deferred": deferred,
            "rejection_reasons": [] if deferred else ([] if passed else ["failed"]),
        },
    }


def _qualification_contract(evaluated: int) -> dict[str, Any]:
    return {
        "schema": "smart_local_qualified_v2",
        "policy": {"policy_version": 2},
        "evaluated_count": evaluated,
        "funnel": {"evaluated": evaluated},
        "rejected_by_reason": {},
        "ratio_policy": {"policy": "soft"},
    }


def _base_kwargs(*, target: int) -> dict[str, Any]:
    return {
        "candidate_limit": 500,
        "limit": target,
        "creator_quota": target,
        "reviewer_quota": 0,
        "required_product_evidence_terms": ["viltrox"],
        "local_qualification_policy": {"policy_version": 2},
    }


def test_shared_local_runtime_forces_provider_free_recall() -> None:
    observed: dict[str, Any] = {}

    def recall(**kwargs: Any) -> dict[str, Any]:
        observed.update(kwargs)
        return {"items": []}

    targeted_search_runtime.execute_local_search(
        context={"query_cells": []},
        recall_kwargs={"provider_free": False, "query_text": "local only"},
        recall=recall,
    )

    assert observed["provider_free"] is True


def test_two_cells_only_passed_proof_counts_and_deferred_is_separate() -> None:
    calls: list[dict[str, Any]] = []

    def recall(**kwargs: Any) -> dict[str, Any]:
        calls.append(dict(kwargs))
        if kwargs["query_text"].startswith("segment 1"):
            items = [
                _item(1, passed=True, score=98),
                _item(2, passed=False, deferred=True, score=95),
            ]
        else:
            items = [
                _item(1, passed=True, score=98),
                _item(3, passed=True, score=94),
                _item(4, passed=False, score=99),
            ]
        return {
            "items": items,
            "query": {"candidate_limit": kwargs["server_candidate_limit_override"]},
            "local_qualification": _qualification_contract(len(items)),
            "diagnostics": {},
        }

    result = targeted_local_recall.execute_first_round_local_cells(
        query_cells=[_cell(1), _cell(2)],
        search_brief={"objective": "existing_evidence"},
        base_kwargs=_base_kwargs(target=2),
        recall=recall,
        target=2,
    )

    assert len(calls) == 2
    assert [call["candidate_limit"] for call in calls] == [60, 60]
    assert [call["server_candidate_limit_override"] for call in calls] == [60, 60]
    assert all(call["candidate_limit"] != 500 for call in calls)
    assert [item["kol_pool_id"] for item in result["items"]] == [1, 3]
    assert all(item["counts_toward_target"] is True for item in result["items"])
    assert all(
        item["qualification_evidence"]["passed"] is True
        and item["qualification_evidence"]["counts_toward_target"] is True
        for item in result["items"]
    )

    assert [item["kol_pool_id"] for item in result["deferred_items"]] == [2]
    deferred = result["deferred_items"][0]
    assert deferred["counts_toward_target"] is False
    assert deferred["qualification_evidence"]["passed"] is False
    assert deferred["qualification_evidence"]["deferred"] is True
    assert deferred["qualification_evidence"]["counts_toward_target"] is False
    assert 4 not in [item["kol_pool_id"] for item in result["items"]]

    contract = result["local_qualification"]
    assert contract["status"] == "ready"
    assert contract["qualified_count"] == 2
    assert contract["qualified_returned_count"] == 2
    assert contract["shortfall"] == 0
    assert contract["funnel_scope"] == "cell_candidate_evaluations"
    assert contract["evaluated_count"] == 5
    assert contract["unique_evaluated"] == 4
    assert contract["unique_qualified"] == 2
    assert contract["deferred_activity"] == {
        "available": 1,
        "returned": 1,
        "counts_toward_target": False,
        "selectable": True,
    }

    budget = result["diagnostics"]["candidate_budget"]
    assert budget["owner"] == "server"
    assert budget["requested_total"] == 120
    assert budget["upstream_cell_candidate_evaluations"] == 5
    assert budget["upstream_within_requested_budget"] is True
    assert budget["aggregation_consumed"] == 5
    assert budget["recall_layer_honored"] is True
    assert budget["enforcement"] == "recall_layer_and_post_recall"


def test_local_candidate_lineage_keeps_server_locked_groups_for_session_replay() -> None:
    cell = _cell(1)
    cell["segment"] = "motorsport"
    cell["locked_term_groups"] = targeted_search_contract.build_locked_term_groups(
        capability="on-camera flash",
        segment="motorsport",
    )

    result = targeted_local_recall.execute_first_round_local_cells(
        query_cells=[cell],
        search_brief={"objective": "existing_evidence"},
        base_kwargs=_base_kwargs(target=1),
        recall=lambda **kwargs: {
            "items": [_item(1, passed=True)],
            "query": {"candidate_limit": kwargs["candidate_limit"]},
            "local_qualification": _qualification_contract(1),
        },
        target=1,
    )

    lineage = result["items"][0]["matched_query_cells"][0]
    assert lineage["locked_term_groups"]["source"] == "server_targeted_contract"
    assert lineage["locked_term_groups"]["groups"][0]["canonical_term"] == "on-camera flash"


def test_deferred_only_never_makes_target_ready_or_qualified() -> None:
    def recall(**kwargs: Any) -> dict[str, Any]:
        item = _item(7, passed=False, deferred=True)
        return {
            "items": [item],
            "query": {"candidate_limit": kwargs["candidate_limit"]},
            "local_qualification": _qualification_contract(1),
        }

    result = targeted_local_recall.execute_first_round_local_cells(
        query_cells=[_cell(1)],
        search_brief={"objective": "existing_evidence"},
        base_kwargs=_base_kwargs(target=1),
        recall=recall,
        target=1,
    )

    assert result["items"] == []
    assert result["match_status"] == "empty"
    assert result["local_qualification"]["status"] == "shortfall"
    assert result["local_qualification"]["qualified_count"] == 0
    assert result["local_qualification"]["qualified_returned_count"] == 0
    assert result["local_qualification"]["shortfall"] == 1
    assert len(result["deferred_items"]) == 1
    assert result["deferred_items"][0]["counts_toward_target"] is False


    # Compatibility post-filters only recount canonical target buckets. The
    # separately displayed deferred zone must still leave the result short.
    filtered = profile_discovery_candidates.filter_recall_result_platforms(
        result, ["youtube"]
    )
    assert filtered["diagnostics"]["returned_count"] == 0
    assert filtered["diagnostics"]["shortfall"] == 1
    assert filtered["diagnostics"]["result_contract_satisfied"] is False


def test_descriptive_activation_score_cannot_replace_explicit_strict_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provisional score is useful for ranking, but never proves activation."""

    monkeypatch.setattr(
        targeted_local_recall.growth_candidate_scoring,
        "score_growth_candidates",
        lambda rows, **_kwargs: [
            {
                **row,
                "product_scene_evidence_pass": True,
                "market_activation": 88.0,
                "market_activation_pass": False,
                "market_activation_status": "insufficient_sample",
            }
            for row in rows
        ],
    )

    result = targeted_local_recall.execute_first_round_local_cells(
        query_cells=[{**_cell(1), "objective": "prospective_growth"}],
        search_brief={"objective": "prospective_growth"},
        base_kwargs=_base_kwargs(target=1),
        recall=lambda **kwargs: {
            "items": [_item(1, passed=True)],
            "query": {"candidate_limit": kwargs["candidate_limit"]},
            "local_qualification": _qualification_contract(1),
        },
        target=1,
    )

    assert result["items"] == []
    assert result["local_qualification"]["qualified_count"] == 0
    assert result["local_qualification"]["rejected_by_reason"][
        "prospective_product_scene_or_activation_missing"
    ] == 1


def test_eight_cells_execute_once_each_with_bounded_fair_budget() -> None:
    calls: list[dict[str, Any]] = []

    def recall(**kwargs: Any) -> dict[str, Any]:
        calls.append(dict(kwargs))
        item_id = int(str(kwargs["query_text"]).split()[1])
        item = _item(item_id, passed=True, score=100 - item_id)
        return {
            "items": [item],
            "query": {"candidate_limit": kwargs["candidate_limit"]},
            "local_qualification": _qualification_contract(1),
        }

    result = targeted_local_recall.execute_first_round_local_cells(
        query_cells=[_cell(index) for index in range(1, 9)],
        search_brief={"objective": "existing_evidence"},
        base_kwargs=_base_kwargs(target=8),
        recall=recall,
        target=8,
    )

    assert len(calls) == 8
    assert [call["candidate_limit"] for call in calls] == [30] * 8
    assert [call["server_candidate_limit_override"] for call in calls] == [30] * 8
    assert sum(call["candidate_limit"] for call in calls) == 240
    assert all(call["candidate_limit"] <= 60 for call in calls)
    assert all(call["candidate_limit"] != 500 for call in calls)
    assert all(call["limit"] == 30 for call in calls)
    assert len(result["items"]) == 8
    assert result["local_qualification"]["status"] == "ready"
    assert result["local_qualification"]["evaluated_count"] == 8
    assert result["local_qualification"]["unique_evaluated"] == 8
    assert result["local_qualification"]["unique_qualified"] == 8
    assert result["local_qualification"]["funnel_scope"] == "cell_candidate_evaluations"

    budget = result["diagnostics"]["candidate_budget"]
    assert budget == {
        "owner": "server",
        "total_cap": 240,
        "per_cell_cap": 60,
        "requested_total": 240,
        "upstream_cell_candidate_evaluations": 8,
        "upstream_within_requested_budget": True,
        "aggregation_consumed": 8,
        "unique_consumed": 8,
        "recall_layer_honored": True,
        "enforcement": "recall_layer_and_post_recall",
    }


def _install_real_recall_budget_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[int], list[int]]:
    retrieval_limits: list[int] = []
    hydration_sizes: list[int] = []

    monkeypatch.setenv("RECALL_LLM_RERANK_ENABLED", "0")
    monkeypatch.setattr(
        profile_recall,
        "resolve_query_text",
        lambda **kwargs: (
            str(kwargs.get("query_text") or "target creator"),
            {"query_profile": "", "query_text_provided": True},
        ),
    )

    def oversized_retrieval(
        _query_text: str,
        candidate_limit: int,
        **_kwargs: Any,
    ) -> list[profile_recall.RecallHit]:
        retrieval_limits.append(candidate_limit)
        # Deliberately violate the helper contract.  profile_recall itself must
        # still slice before any row/evidence hydration.
        return [
            profile_recall.RecallHit(index, 1.0, f"point-{index}")
            for index in range(1, 501)
        ]

    def entry_rows(ids: list[int]) -> dict[int, dict[str, Any]]:
        hydration_sizes.append(len(ids))
        return {}

    monkeypatch.setattr(profile_recall, "_pool_text_fallback_hits", oversized_retrieval)
    monkeypatch.setattr(profile_recall, "_entry_rows", entry_rows)
    monkeypatch.setattr(profile_recall, "_evidence_summaries", lambda _ids: {})
    monkeypatch.setattr(profile_recall, "_pool_rows_fallback", lambda _ids: {})
    monkeypatch.setattr(
        profile_recall,
        "_smart_local_qualification_context",
        lambda _ids, *, rows_by_id, evidence_by_id: (rows_by_id, evidence_by_id),
    )
    monkeypatch.setattr(profile_recall, "_adoption_profile", lambda: {})
    monkeypatch.setattr(
        profile_recall._favorite_exclusion,
        "exclude_favorited_hits",
        lambda hits: (list(hits), {"excluded_count": 0}),
    )
    return retrieval_limits, hydration_sizes


@pytest.mark.parametrize(
    ("cell_count", "expected_per_cell"),
    [(2, 60), (8, 30)],
)
def test_real_smart_local_retrieval_and_hydration_obey_multicell_budget(
    monkeypatch: pytest.MonkeyPatch,
    cell_count: int,
    expected_per_cell: int,
) -> None:
    retrieval_limits, hydration_sizes = _install_real_recall_budget_probe(monkeypatch)
    policy = profile_recall_qualification.smart_local_policy(
        market="",
        platforms=["youtube"],
    )

    result = targeted_local_recall.execute_first_round_local_cells(
        query_cells=[_cell(index) for index in range(1, cell_count + 1)],
        search_brief={"objective": "existing_evidence"},
        base_kwargs={
            **_base_kwargs(target=min(cell_count, 30)),
            "provider_free": True,
            # Exercise the filtered-search oversampling branch too: the
            # internal server budget must remain authoritative.
            "filters": {"platforms": ["youtube"]},
            "local_qualification_policy": policy,
        },
        recall=profile_recall.recall_kol_profiles,
        target=min(cell_count, 30),
    )

    assert retrieval_limits == [expected_per_cell] * cell_count
    assert hydration_sizes == [expected_per_cell] * cell_count
    assert 500 not in retrieval_limits
    assert 500 not in hydration_sizes
    assert result["query"]["candidate_limit"] == expected_per_cell
    assert result["query"]["server_candidate_limit_override"] == expected_per_cell
    assert result["query"]["server_candidate_limit_override_applied"] is True
    assert [
        run["candidate_limit_effective"]
        for run in result["diagnostics"]["targeted_cell_runs"]
    ] == [expected_per_cell] * cell_count
    assert result["diagnostics"]["candidate_budget"]["recall_layer_honored"] is True


def test_server_candidate_override_is_ignored_outside_smart_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retrieval_limits, hydration_sizes = _install_real_recall_budget_probe(monkeypatch)

    result = profile_recall.recall_kol_profiles(
        query_text="target creator",
        provider_free=True,
        candidate_limit=7,
        limit=1,
        creator_quota=1,
        reviewer_quota=0,
        server_candidate_limit_override=2,
    )

    assert retrieval_limits == [7]
    assert hydration_sizes == [7]
    assert result["query"]["candidate_limit"] == 7
    assert result["query"]["server_candidate_limit_override"] is None
    assert result["query"]["server_candidate_limit_override_applied"] is False
