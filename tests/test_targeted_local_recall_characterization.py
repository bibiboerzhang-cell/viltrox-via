from __future__ import annotations

from typing import Any

import pytest

from app.domains.kol import targeted_local_recall


def _cell(index: int) -> dict[str, Any]:
    return {
        "query_cell_id": f"cell-{index}",
        "objective": "prospective_growth",
        "segment": f"segment-{index}",
        "segment_label": f"Segment {index}",
        "primary_query": f"query {index}",
        "raw_limit": 1,
        "required_evidence_groups": ["product_use_fit"],
        "brand_or_model_required": False,
    }


def test_no_normalized_cells_returns_recall_fallback_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[Any] = []
    fallback = {"items": [{"kol_pool_id": 91}], "method": "legacy"}
    base_kwargs = {"query_text": "legacy local", "limit": 4, "provider_free": True}

    monkeypatch.setattr(
        targeted_local_recall.targeted_query_execution,
        "normalize_first_round_cells",
        lambda value: events.append(("normalize", value)) or ([], 2),
    )
    monkeypatch.setattr(
        targeted_local_recall.growth_candidate_scoring,
        "score_growth_candidates",
        lambda *_args, **_kwargs: pytest.fail("fallback must not score candidates"),
    )

    def recall(**kwargs: Any) -> dict[str, Any]:
        events.append(("recall", kwargs))
        return fallback

    result = targeted_local_recall.execute_first_round_local_cells(
        query_cells="not-a-list",
        search_brief={"objective": "prospective_growth"},
        base_kwargs=base_kwargs,
        recall=recall,
        target="invalid",  # type: ignore[arg-type]
    )

    assert result is fallback
    assert events == [
        ("normalize", "not-a-list"),
        ("recall", base_kwargs),
    ]


def test_two_cell_stage_order_and_response_projection_are_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cells = [_cell(1), _cell(2)]
    events: list[Any] = []

    monkeypatch.setattr(
        targeted_local_recall.targeted_query_execution,
        "normalize_first_round_cells",
        lambda value: events.append(("normalize", value)) or (cells, 1),
    )

    def recall(**kwargs: Any) -> dict[str, Any]:
        cell_id = kwargs["targeted_query_cell"]["query_cell_id"]
        events.append(("recall", cell_id, kwargs["candidate_limit"]))
        item_id = int(cell_id.rsplit("-", 1)[1])
        return {
            "method": "legacy",
            "provider_trace": f"provider-{item_id}",
            "items": [
                {
                    "kol_pool_id": item_id,
                    "platform": "youtube",
                    "handle": f"creator-{item_id}",
                    "bucket": "creator",
                    "display_rank_score": 80 - item_id,
                    "qualification_evidence": {"passed": True, "deferred": False},
                }
            ],
            "query": {
                "candidate_limit": kwargs["candidate_limit"],
                "first_query_marker": item_id,
            },
            "ranking": {"legacy_ranker": item_id},
            "diagnostics": {"upstream_marker": item_id},
            "local_qualification": {
                "schema": "smart_local_qualified_v2",
                "evaluated_count": 1,
                "funnel": {"evaluated": 1},
                "rejected_by_reason": {},
                "ratio_policy": {"policy": "soft"},
            },
        }

    def score_growth_candidates(
        rows: list[dict[str, Any]],
        *,
        search_brief: dict[str, Any],
        query_cell: dict[str, Any],
    ) -> list[dict[str, Any]]:
        events.append(
            (
                "score",
                query_cell["query_cell_id"],
                search_brief["objective"],
            )
        )
        return [
            {
                **row,
                "growth_candidate_score": 100 - int(row["kol_pool_id"]),
                "product_scene_evidence_pass": True,
                "market_activation_pass": True,
            }
            for row in rows
        ]

    monkeypatch.setattr(
        targeted_local_recall.growth_candidate_scoring,
        "score_growth_candidates",
        score_growth_candidates,
    )

    result = targeted_local_recall.execute_first_round_local_cells(
        query_cells=[{"client": "raw"}],
        search_brief={"objective": "prospective_growth"},
        base_kwargs={"creator_quota": 2, "reviewer_quota": 0},
        recall=recall,
        target=2,
    )

    assert events == [
        ("normalize", [{"client": "raw"}]),
        ("recall", "cell-1", 60),
        ("score", "cell-1", "prospective_growth"),
        ("recall", "cell-2", 60),
        ("score", "cell-2", "prospective_growth"),
    ]
    assert result["method"] == "targeted_local_query_cells_v1"
    assert result["provider_trace"] == "provider-1"
    assert [item["kol_pool_id"] for item in result["items"]] == [1, 2]
    assert [
        item["matched_query_cells"][0]["query_cell_id"]
        for item in result["items"]
    ] == ["cell-1", "cell-2"]
    assert result["query"] == {
        "candidate_limit": 60,
        "first_query_marker": 1,
        "query_text": "query 1",
        "query_cells": [
            targeted_local_recall._cell_context(cells[0]),
            targeted_local_recall._cell_context(cells[1]),
        ],
        "query_mode": "independent_query_cells",
    }
    assert result["diagnostics"]["upstream_marker"] == 1
    assert result["diagnostics"]["query_cells_requested"] == 3
    assert result["diagnostics"]["query_cells_executed"] == 2
    assert result["diagnostics"]["candidate_budget"]["requested_total"] == 120
    assert [
        run["query_cell_id"]
        for run in result["diagnostics"]["targeted_cell_runs"]
    ] == ["cell-1", "cell-2"]
    assert result["local_qualification"]["status"] == "ready"
    assert result["local_qualification"]["unique_evaluated"] == 2
