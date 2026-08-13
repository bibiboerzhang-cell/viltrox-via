from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.api.routers import vkpi_kol_pool_search
from app.domains.kol import profile_discovery_candidates, profile_discovery_pipeline


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("find uk photographers", "gb"),
        ("find jp creators", "jp"),
        ("find br filmmakers", "br"),
        ("find mx creators", "mx"),
    ],
)
def test_unambiguous_lowercase_market_codes_are_operator_constraints(
    query: str,
    expected: str,
) -> None:
    assert profile_discovery_candidates.explicit_market_constraint(query, None) == expected


@pytest.mark.parametrize(
    "query",
    [
        "find creators for us",
        "show us portrait creators",
        "photographes de mariage",
        "portrait au flash",
        "EPIC 65mm PL mount cinematographers",
    ],
)
def test_ambiguous_lowercase_words_and_product_mounts_are_not_markets(query: str) -> None:
    assert profile_discovery_candidates.explicit_market_constraint(query, None) == ""


def test_in_pl_mount_is_product_syntax_but_in_pl_is_poland() -> None:
    assert profile_discovery_candidates.explicit_market_constraint(
        "find filmmakers using EPIC 65mm in PL mount",
        None,
    ) == ""
    assert profile_discovery_candidates.explicit_market_constraint(
        "find filmmakers in PL",
        None,
    ) == "pl"


def _candidate(item_id: int, *, country: str, handle: str) -> dict[str, Any]:
    return {
        "kol_pool_id": item_id,
        "handle": handle,
        "display_name": handle.replace("-", " ").title(),
        "platform": "youtube",
        "candidate_facets": {
            "platform": "youtube",
            "country": country,
            "language": "en",
            "profile_type": "creator",
            "contact_available": "unknown",
            "video_evidence": "no",
        },
    }


@pytest.mark.parametrize(
    ("query", "expected_market"),
    [
        ("find uk photographers", "gb"),
        ("find jp creators", "jp"),
    ],
)
def test_lowercase_market_is_a_hard_filter_in_preview_route_and_worker(
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    expected_market: str,
) -> None:
    expected = _candidate(701, country=expected_market, handle=f"{expected_market}-creator")
    unrelated = _candidate(702, country="us", handle="us-creator")

    def recall_result(**_kwargs: Any) -> dict[str, Any]:
        return {
            "method": "provider_free_pool_text",
            "match_status": "matched",
            "items": [expected, unrelated],
            "buckets": {"creator": [expected, unrelated], "reviewer": []},
            "diagnostics": {
                "returned_count": 2,
                "creator_returned": 2,
                "reviewer_returned": 0,
                "evidence_gate_enabled": True,
            },
        }

    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_smart_query_planner,
        "plan_text_query_provider_free",
        lambda *_args, **_kwargs: {
            "status": "ready",
            "search_query": "photographer creator",
            "product_focus": ["photographer"],
            "target_persona": "Working photographers",
            "market": "US",
            "provider_calls_performed": False,
        },
    )
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_profile_recall,
        "recall_kol_profiles",
        recall_result,
    )
    monkeypatch.setattr(
        vkpi_kol_pool_search,
        "_attach_smart_recall_session",
        lambda **kwargs: kwargs["result"],
    )

    preview = asyncio.run(
        vkpi_kol_pool_search.smart_kol_search(
            {"input": query, "create_session": False},
            staff={"id": 42},
        )
    )
    preview_result = preview["result"]
    assert [item["kol_pool_id"] for item in preview_result["items"]] == [701]
    assert preview_result["market_filter"] == {
        "applied": True,
        "requested": expected_market,
    }
    assert preview_result["candidate_set_distribution"]["denominator"] == 1

    attached: dict[str, Any] = {}

    def attach_recall(session_id: int, result: dict[str, Any]) -> dict[str, Any]:
        attached.update(session_id=session_id, result=result)
        return {"id": session_id, "items": result.get("items") or []}

    monkeypatch.setattr(
        profile_discovery_pipeline.search_sessions,
        "attach_recall_result",
        attach_recall,
    )
    monkeypatch.setattr(
        profile_discovery_pipeline.search_sessions,
        "update_session_result_summary",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        profile_discovery_pipeline,
        "advance_search_session_items",
        lambda **_kwargs: {
            "status": "empty",
            "selected": 0,
            "items": [],
            "counts": {},
            "viltrox_fit_score_changed_ids": [],
        },
    )

    worker = asyncio.run(
        profile_discovery_pipeline.execute_smart_search_profile_advance_pipeline(
            session_id=9902,
            payload={
                "query_text": query,
                "_worker_planned": True,
                "include_new_discovery": False,
                "include_content_fit": False,
                "include_lazy_video_backfill": False,
            },
        )
    )

    worker_result = attached["result"]
    assert attached["session_id"] == 9902
    assert [item["kol_pool_id"] for item in worker_result["items"]] == [701]
    assert worker_result["market_filter"] == {
        "applied": True,
        "requested": expected_market,
    }
    assert worker_result["candidate_set_distribution"]["denominator"] == 1
    assert worker["recall"]["returned_count"] == 1


@pytest.mark.parametrize(
    ("query", "expected_market", "expected_ids"),
    [
        ("find filmmakers using EPIC 65mm in PL mount", "", [801, 802]),
        ("find filmmakers in PL", "pl", [801]),
    ],
)
def test_pl_mount_context_controls_preview_and_worker_market_filtering(
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    expected_market: str,
    expected_ids: list[int],
) -> None:
    poland = _candidate(801, country="pl", handle="poland-filmmaker")
    united_states = _candidate(802, country="us", handle="us-filmmaker")

    def recall_result(**_kwargs: Any) -> dict[str, Any]:
        return {
            "method": "provider_free_pool_text",
            "match_status": "matched",
            "items": [poland, united_states],
            "buckets": {"creator": [poland, united_states], "reviewer": []},
            "diagnostics": {
                "returned_count": 2,
                "creator_returned": 2,
                "reviewer_returned": 0,
                "evidence_gate_enabled": True,
            },
        }

    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_smart_query_planner,
        "plan_text_query_provider_free",
        lambda *_args, **_kwargs: {
            "status": "ready",
            "search_query": "filmmaker cinematographer",
            "product_focus": ["filmmaker"],
            "target_persona": "Working filmmakers",
            "market": "US",
            "provider_calls_performed": False,
        },
    )
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_profile_recall,
        "recall_kol_profiles",
        recall_result,
    )
    monkeypatch.setattr(
        vkpi_kol_pool_search,
        "_attach_smart_recall_session",
        lambda **kwargs: kwargs["result"],
    )

    preview = asyncio.run(
        vkpi_kol_pool_search.smart_kol_search(
            {"input": query, "create_session": False},
            staff={"id": 42},
        )
    )["result"]

    assert [item["kol_pool_id"] for item in preview["items"]] == expected_ids
    if expected_market:
        assert preview["market_filter"] == {
            "applied": True,
            "requested": expected_market,
        }
        assert preview["candidate_set_distribution"]["denominator"] == 1
    else:
        assert "market_filter" not in preview

    attached: dict[str, Any] = {}

    def attach_recall(session_id: int, result: dict[str, Any]) -> dict[str, Any]:
        attached.update(session_id=session_id, result=result)
        return {"id": session_id, "items": result.get("items") or []}

    monkeypatch.setattr(
        profile_discovery_pipeline.search_sessions,
        "attach_recall_result",
        attach_recall,
    )
    monkeypatch.setattr(
        profile_discovery_pipeline.search_sessions,
        "update_session_result_summary",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        profile_discovery_pipeline,
        "advance_search_session_items",
        lambda **_kwargs: {
            "status": "empty",
            "selected": 0,
            "items": [],
            "counts": {},
            "viltrox_fit_score_changed_ids": [],
        },
    )

    worker = asyncio.run(
        profile_discovery_pipeline.execute_smart_search_profile_advance_pipeline(
            session_id=9903,
            payload={
                "query_text": query,
                "_worker_planned": True,
                "include_new_discovery": False,
                "include_content_fit": False,
                "include_lazy_video_backfill": False,
            },
        )
    )
    worker_result = attached["result"]

    assert attached["session_id"] == 9903
    assert [item["kol_pool_id"] for item in worker_result["items"]] == expected_ids
    if expected_market:
        assert worker_result["market_filter"] == {
            "applied": True,
            "requested": expected_market,
        }
        assert worker_result["candidate_set_distribution"]["denominator"] == 1
    else:
        assert "market_filter" not in worker_result
    assert worker["recall"]["returned_count"] == len(expected_ids)
