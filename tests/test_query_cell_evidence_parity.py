from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.domains.kol import (
    growth_candidate_scoring,
    profile_online_qualification,
    profile_recall,
    profile_recall_qualification,
    targeted_local_recall,
    targeted_search_contract,
)
from app.domains.kol.profile_query_cell_evidence import build_query_cell_match_evidence


AS_OF = datetime(2026, 8, 27, tzinfo=timezone.utc)
CONTROLLED_SOURCES = {
    "server_allowlisted_alias_evidence",
    "server_capability_use_map",
}


def _cell(capability: str, segment: str, query: str) -> dict[str, Any]:
    return {
        "query_cell_id": f"cell-{segment}",
        "objective": "prospective_growth",
        "segment": segment,
        "segment_label": segment,
        "primary_query": query,
        "platforms": ["youtube"],
        "round": 1,
        "raw_limit": 10,
        "required_evidence_groups": [
            "product_use_fit",
            "segment_use_case",
            "market_activation",
        ],
        "locked_term_groups": targeted_search_contract.build_locked_term_groups(
            capability=capability,
            segment=segment,
            segment_label=segment,
        ),
    }


def _row(item_id: int, phrase: str) -> dict[str, Any]:
    return {
        "kol_pool_id": item_id,
        "handle": f"creator-{item_id}",
        "display_name": "Independent Creator",
        "platform": "youtube",
        "profile_url": f"https://www.youtube.com/@creator-{item_id}",
        "followers": 120_000,
        "avg_views": 60_000,
        "avg_comments": 800,
        "engagement_rate": 0.08,
        "country": "US",
        "language": "en",
        "profile_type": "creator",
        "creator_type_score": 90,
        "reviewer_type_score": 10,
        "bio": f"Independent {phrase}",
        "profile_text": f"Independent {phrase}",
        "primary_topic": phrase,
        "content_style": "field tutorial",
        "type_reason": "fixture",
        "raw_platform_data": {},
    }


def _evidence(item_id: int) -> dict[str, Any]:
    return {
        "representative_evidence": [{
            "title": "Independent field workflow",
            "content_url": f"https://www.youtube.com/watch?v=parity{item_id}",
        }],
        "latest_real_video": {
            "posted_at": "2026-08-25T00:00:00Z",
            "evidence_type": "video",
            "content_url": f"https://www.youtube.com/watch?v=parity{item_id}",
            "source": "vkpi_kol_video_evidence.posted_at",
        },
        "video_evidence_count": 4,
        "with_view_count": 4,
        "deep_analysis_count": 0,
    }


def _install_local_recall(
    monkeypatch: pytest.MonkeyPatch,
    row: dict[str, Any],
    evidence: dict[str, Any],
) -> None:
    monkeypatch.setenv("RECALL_LLM_RERANK_ENABLED", "0")
    monkeypatch.setattr(
        profile_recall,
        "resolve_query_text",
        lambda **kwargs: (
            str(kwargs.get("query_text") or ""),
            {"query_profile": "", "query_text_provided": True},
        ),
    )
    monkeypatch.setattr(
        profile_recall,
        "_pool_text_fallback_hits",
        lambda *_args, **_kwargs: [
            profile_recall.RecallHit(int(row["kol_pool_id"]), 0.9, "parity-point")
        ],
    )
    monkeypatch.setattr(profile_recall, "_entry_rows", lambda _ids: {int(row["kol_pool_id"]): dict(row)})
    monkeypatch.setattr(
        profile_recall,
        "_evidence_summaries",
        lambda _ids: {int(row["kol_pool_id"]): dict(evidence)},
    )
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


def _controlled_coordinates(rows: list[dict[str, Any]]) -> set[tuple[str, str, str, str]]:
    return {
        (
            str(row.get("source") or ""),
            str(row.get("canonical_term") or ""),
            str(row.get("observed_term") or ""),
            str(row.get("evidence_group") or ""),
        )
        for row in rows
        if row.get("source") in CONTROLLED_SOURCES
    }


@pytest.mark.parametrize(
    ("item_id", "capability", "segment", "query", "profile_phrase"),
    [
        (701, "on-camera flash", "food", "food photographer tutorial camera gear", "food photographer"),
        (702, "telephoto portrait lens", "stage", "stage performance photographer tutorial camera gear", "concert photographer"),
    ],
)
def test_local_and_online_share_server_controlled_product_scene_determination(
    monkeypatch: pytest.MonkeyPatch,
    item_id: int,
    capability: str,
    segment: str,
    query: str,
    profile_phrase: str,
) -> None:
    cell = _cell(capability, segment, query)
    row = _row(item_id, profile_phrase)
    evidence = _evidence(item_id)
    _install_local_recall(monkeypatch, row, evidence)

    local = profile_recall.recall_kol_profiles(
        query_text=query,
        provider_free=True,
        candidate_limit=10,
        limit=1,
        creator_quota=1,
        reviewer_quota=0,
        local_qualification_policy=profile_recall_qualification.smart_local_policy(
            market="",
            platforms=["youtube"],
        ),
        server_candidate_limit_override=10,
        targeted_query_cell=cell,
    )
    assert len(local["items"]) == 1
    local_match = local["items"][0]["match_evidence"]

    online_raw = {
        **row,
        "channel_id": f"UCparity{item_id}",
        "country_source": "platform_profile",
        "language_source": "platform_profile",
        "profile_type_source": "provider_declared",
        "latest_real_video": evidence["latest_real_video"],
        "matched_query_cells": [cell],
    }
    adapted, _rows, _adapted_evidence, _sources, cell_inputs = (
        profile_online_qualification._adapt_candidates([online_raw], query_text=query)
    )
    online_match = cell_inputs[adapted[0]["kol_pool_id"]][0]["match_evidence"]

    assert _controlled_coordinates(local_match) == _controlled_coordinates(online_match)
    assert _controlled_coordinates(local_match)
    brief = {"objective": "prospective_growth", "product": {"capability": capability}}
    [local_scored] = growth_candidate_scoring.score_growth_candidates(
        [{**local["items"][0], "activation_sample_count": 4}], brief, cell
    )
    [online_scored] = growth_candidate_scoring.score_growth_candidates(
        [{**adapted[0], "match_evidence": online_match, "activation_sample_count": 4}],
        brief,
        cell,
    )
    assert local_scored["product_scene_evidence_pass"] is True
    assert online_scored["product_scene_evidence_pass"] is True
    assert local_scored["claim_status"] == online_scored["claim_status"] == "descriptive_only"
    assert local_scored["growth_candidate_scoring"]["brand_history_weight"] == 0
    assert online_scored["growth_candidate_scoring"]["brand_history_weight"] == 0


def test_targeted_local_passes_normalized_server_query_cell_into_recall() -> None:
    cell = _cell(
        "on-camera flash",
        "food",
        "food photographer tutorial camera gear",
    )
    observed: list[dict[str, Any]] = []

    def recall(**kwargs: Any) -> dict[str, Any]:
        observed.append(dict(kwargs))
        query_cell = kwargs["targeted_query_cell"]
        match = profile_online_qualification._cell_match_evidence(
            {"bio": "Independent food photographer"},
            {},
            query_text=kwargs["query_text"],
            query_cell=query_cell,
        )
        return {
            "items": [{
                "kol_pool_id": 801,
                "platform": "youtube",
                "handle": "food-801",
                "followers": 120_000,
                "avg_views": 60_000,
                "avg_comments": 800,
                "engagement_rate": 0.08,
                "activation_sample_count": 4,
                "bucket": "creator",
                "match_evidence": match,
                "qualification_evidence": {"passed": True, "deferred": False},
            }],
            "query": {"candidate_limit": kwargs["candidate_limit"]},
            "local_qualification": {
                "schema": "smart_local_qualified_v2",
                "evaluated_count": 1,
                "funnel": {"evaluated": 1},
                "rejected_by_reason": {},
                "ratio_policy": {"policy": "soft"},
            },
        }

    result = targeted_local_recall.execute_first_round_local_cells(
        query_cells=[cell],
        search_brief={
            "objective": "prospective_growth",
            "product": {"capability": "on-camera flash"},
        },
        base_kwargs={
            "candidate_limit": 500,
            "limit": 1,
            "creator_quota": 1,
            "reviewer_quota": 0,
        },
        recall=recall,
        target=1,
    )

    assert observed[0]["targeted_query_cell"]["locked_term_groups"]["source"] == (
        "server_targeted_contract"
    )
    assert result["items"][0]["product_scene_evidence_pass"] is True
    assert result["items"][0]["claim_status"] == "descriptive_only"


def test_shared_evidence_builder_rejects_client_injected_aliases() -> None:
    cell = _cell(
        "on-camera flash",
        "motorsport",
        "motorsport photographer tutorial camera gear",
    )
    cell["locked_term_groups"]["groups"][0]["aliases"].append("zoomlight")
    cell["locked_term_groups"]["groups"][1]["aliases"].append("trackshoot")

    evidence = build_query_cell_match_evidence(
        {"bio": "Independent zoomlight trackshoot creator"},
        {},
        cell["primary_query"],
        query_cell=cell,
    )

    assert _controlled_coordinates(evidence) == set()
