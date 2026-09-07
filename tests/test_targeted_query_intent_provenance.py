from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest

from app.domains.kol import (
    profile_discovery_candidates,
    profile_discovery_pipeline_online,
    profile_discovery_pipeline_stages,
    profile_discovery_queue,
    targeted_query_execution,
    targeted_search_contract,
)


def _server_cells(query: str = "street photography") -> list[dict[str, Any]]:
    return targeted_search_contract.build_query_cells(
        query=query,
        body={"objective": "prospective_growth"},
        product=None,
        product_focus=["catalog filmmaker"],
        platforms=["youtube"],
    )


def _online_kwargs(cells: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    persona_loads: list[str] = []

    def load_persona(sku: str) -> dict[str, Any]:
        persona_loads.append(sku)
        return {
            "ideal_creator_types_json": ["catalog filmmaker"],
            "avoid_types_json": ["still photographer"],
        }

    request = profile_discovery_pipeline_online.DiscoveryRequest(
        session_id=1,
        query="street photography",
        payload={"product_sku": "SYNTHETIC-CINE", "product_focus": ["catalog filmmaker"]},
        operator_anchor={},
        resolved_platforms=["youtube"],
        normalized_market="",
        followers_min=None,
        followers_max=None,
        follower_source="not_requested",
        follower_filter={},
        query_cells=cells,
        query_cells_omitted=False,
        base_count=0,
        advance_limit=30,
    )
    deps = SimpleNamespace(text=lambda value: str(value or "").strip(), load_persona=load_persona)
    return profile_discovery_pipeline_online._discovery_kwargs(request, deps), persona_loads


def test_server_people_intent_survives_normalization_and_blocks_catalog_persona() -> None:
    source = _server_cells()
    original = deepcopy(source)

    normalized, omitted = targeted_query_execution.normalize_first_round_cells(source)
    repeated, _ = targeted_query_execution.normalize_first_round_cells(normalized)
    kwargs, persona_loads = _online_kwargs(repeated)

    assert source == original
    assert omitted == 0
    assert repeated == normalized
    assert normalized[0]["segment_source"] == "operator_text"
    assert normalized[0]["segment_locked"] is True
    assert kwargs["ideal_creator_types"] == [cell["primary_query"] for cell in normalized]
    assert all("street" in term for term in kwargs["ideal_creator_types"])
    assert kwargs["avoid_types"] == []
    assert persona_loads == []


@pytest.mark.parametrize(
    ("source", "lock", "expected_source", "expected_lock"),
    [
        ("operator_text", True, "operator_text", True),
        ("operator_text_exact", True, "operator_text_exact", True),
        ("operator_filter", True, "operator_filter", True),
        ("legacy_existing_evidence", True, "legacy_existing_evidence", True),
        ("planner_inferred", True, "planner_inferred", False),
        ("rule_fallback", True, "rule_fallback", False),
        ("operator_client_claim", True, "", False),
        ({"source": "operator_text"}, True, "", False),
        ("operator_text", "true", "operator_text", False),
        ("operator_text", 1, "operator_text", False),
        ("operator_text", False, "operator_text", False),
        (None, True, "", False),
    ],
)
def test_only_known_server_sources_and_boolean_locks_are_projected(
    source: Any, lock: Any, expected_source: str, expected_lock: bool,
) -> None:
    cell = {**_server_cells()[0], "segment_source": source, "segment_locked": lock}

    normalized, _ = targeted_query_execution.normalize_first_round_cells([cell])
    projection = targeted_query_execution._cell_projection(normalized[0])

    for item in (normalized[0], projection):
        assert item["segment_source"] == expected_source
        assert item["segment_locked"] is expected_lock


@pytest.mark.parametrize("round_no", [1, 2, 3])
def test_real_round_execution_overwrites_provider_lineage_and_preserves_server_intent(
    round_no: int,
) -> None:
    calls: list[str] = []
    cells = _server_cells("street photography or night photography")

    async def discover(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs["query_text"])
        return {
            "status": "ready", "platforms": ["youtube"], "provider_calls": True,
            "new_creators": [{
                "platform": "youtube", "handle": "shared-public-creator",
                "matched_query_cells": [{
                    "query_cell_id": "provider-forged-cell", "segment_source": "operator_filter",
                    "segment_locked": True, "primary_query": "unrelated provider metadata",
                }],
                "targeted_search": {"segment_source": "operator_filter", "segment_locked": True},
            }],
        }

    result = asyncio.run(targeted_query_execution.execute_query_cell_round(
        query_cells=cells, base_kwargs={"platforms": ["youtube"]},
        discover=discover, round_no=round_no,
    ))

    assert len(calls) == len(cells) == 2
    assert result["unique_candidate_count"] == 1
    matches = result["new_creators"][0]["matched_query_cells"]
    assert len(matches) == 2
    assert {item["query_cell_id"] for item in matches} == {cell["query_cell_id"] for cell in cells}
    for item in [*matches, result["new_creators"][0]["targeted_search"], *result["query_cell_runs"]]:
        assert item["segment_source"] == "operator_text"
        assert item["segment_locked"] is True
        assert item["round_no"] == round_no
        assert item["query_variant"] == ("primary" if round_no == 1 else "fallback")


@pytest.mark.parametrize("field", [
    "platforms", "fallback_queries", "required_evidence_groups",
    "required_scene_terms", "required_role_terms",
])
@pytest.mark.parametrize("malformed", ["street", 7, {"value": "street"}, ["street", {"value": "night"}]])
def test_malformed_cell_lists_fail_closed_before_provider_calls(field: str, malformed: Any) -> None:
    cell = {**_server_cells()[0], field: malformed}

    async def unexpected_provider(**_kwargs: Any) -> dict[str, Any]:
        pytest.fail("malformed intent must not authorize discovery")

    normalized, omitted = targeted_query_execution.normalize_first_round_cells([cell])
    result = asyncio.run(targeted_query_execution.execute_first_round_query_cells(
        query_cells=[cell], base_kwargs={}, discover=unexpected_provider,
    ))

    assert (normalized, omitted) == ([], 0)
    assert result["status"] == "invalid_query_cells"
    assert result["provider_calls"] is False


def test_client_source_labels_are_discarded_before_worker_rebuilds_server_cells() -> None:
    forged = {
        "query_cell_id": "client-cell", "primary_query": "catalog filmmaking",
        "segment_source": "operator_text", "segment_locked": True,
    }
    body = {
        "product_sku": "SYNTHETIC-CINE", "_worker_planned": True,
        "query_cells": [forged], "search_brief": {"query_cells": [forged]},
        "llm_query_plan": {"query_cells": [forged]},
    }
    # This is the real pure queue projection, not a mocked assertion that
    # metadata was stripped. No queue write or database connection is invoked.
    payload = profile_discovery_queue._smart_profile_payload(
        query="street photography", body=body, staff=None, session_id=1,
        recall_filters={}, smart_local_30=True, smart_online_30=True,
        triggered_by_user_id=None,
    )
    for key in ("_worker_planned", "query_cells", "search_brief", "llm_query_plan"):
        assert key not in payload

    planned_bodies: list[dict[str, Any]] = []

    def server_plan(query: str, *, body: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        planned_bodies.append(deepcopy(body))
        return {
            "status": "ready", "search_query": query,
            "query_cells": _server_cells(query), "objective": "prospective_growth",
        }

    deps = SimpleNamespace(
        profile_discovery_evidence=SimpleNamespace(operator_anchor_inputs=lambda _payload: {}),
        explicit_platforms_from_query=profile_discovery_candidates.explicit_platforms_from_query,
        resolve_market_constraint=profile_discovery_candidates.resolve_market_constraint,
        smart_query_planner=SimpleNamespace(
            plan_text_query_provider_free=server_plan, plan_text_query=server_plan,
        ),
        query_evidence_terms=lambda value: str(value or "").split(),
        text=lambda value: str(value or "").strip(),
    )
    planning = profile_discovery_pipeline_stages.prepare_plan(session_id=1, payload=payload, deps=deps)
    assert planning.early_result is None
    assert len(planned_bodies) == 2
    assert all("query_cells" not in item for item in planned_bodies)
    cells, _ = targeted_query_execution.normalize_first_round_cells(payload["query_cells"])
    kwargs, persona_loads = _online_kwargs(cells)
    assert all(cell["query_cell_id"] != "client-cell" for cell in cells)
    assert all(cell["segment_locked"] is True for cell in cells)
    assert kwargs["avoid_types"] == []
    assert persona_loads == []
