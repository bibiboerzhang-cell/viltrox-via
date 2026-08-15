from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.domains.kol import (
    profile_discovery_pipeline,
    profile_recall_match_evidence,
    smart_query_planner,
)


class _Rows:
    def __init__(self, rows: list[dict[str, int]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, int]]:
        return list(self._rows)


def test_legacy_exact_phrase_returns_exact_hit_without_popularity_fill() -> None:
    class _Conn:
        calls = 0

        def execute(self, _sql: str, _params: tuple[Any, ...]) -> _Rows:
            self.calls += 1
            if self.calls == 1:
                return _Rows([{"kol_pool_id": 1}])
            raise AssertionError("legacy exact hit must return before follower-head fill")

    conn = _Conn()
    ids = profile_recall_match_evidence.pool_text_fallback_ids(
        conn,
        "lens review",
        30,
        max_candidate_limit=500,
        allow_backfill=True,
    )

    assert ids == [1]
    assert conn.calls == 1


def test_smart_exact_phrase_uses_anchor_top_up_without_popularity_fill() -> None:
    class _Conn:
        calls = 0

        def execute(self, _sql: str, _params: tuple[Any, ...]) -> _Rows:
            self.calls += 1
            if self.calls == 1:
                return _Rows([{"kol_pool_id": 1}])
            if self.calls == 2:
                return _Rows([{"kol_pool_id": 1}, {"kol_pool_id": 2}, {"kol_pool_id": 3}])
            raise AssertionError("strict Smart lane must never query follower-head backfill")

    conn = _Conn()
    ids = profile_recall_match_evidence.pool_text_fallback_ids(
        conn,
        "lens review",
        3,
        max_candidate_limit=500,
        allow_backfill=False,
    )

    assert ids == [1, 2, 3]
    assert conn.calls == 2


@pytest.mark.parametrize(
    "rich_query",
    [
        pytest.param("portrait fashion creator", id="no-guard-overlap"),
        pytest.param("lens fashion creator", id="partial-guard-overlap"),
    ],
)
def test_worker_rejects_rich_query_that_drops_any_guard_anchor(
    monkeypatch,
    rich_query: str,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        smart_query_planner,
        "plan_text_query_provider_free",
        lambda *_args, **_kwargs: {
            "status": "fallback",
            "search_query": "lens review photographer",
            "product_focus": ["lens review"],
        },
    )
    monkeypatch.setattr(
        smart_query_planner,
        "plan_text_query",
        lambda *_args, **_kwargs: {
            "status": "ready",
            "search_query": rich_query,
            "product_focus": [rich_query],
        },
    )
    monkeypatch.setattr(
        profile_discovery_pipeline.profile_recall,
        "recall_kol_profiles",
        lambda **kwargs: captured.update(kwargs) or {
            "items": [],
            "buckets": {"creator": [], "reviewer": []},
            "diagnostics": {"returned_count": 0},
            "local_qualification": {"returned_count": 0, "shortfall": 30},
        },
    )
    monkeypatch.setattr(
        profile_discovery_pipeline,
        "filter_recall_result_platforms",
        lambda result, _value: result,
    )
    monkeypatch.setattr(
        profile_discovery_pipeline,
        "filter_recall_result_market",
        lambda result, _value: result,
    )
    monkeypatch.setattr(
        profile_discovery_pipeline.search_sessions,
        "attach_recall_result",
        lambda *_args: {},
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
            "counts": {},
            "items": [],
            "viltrox_fit_score_changed_ids": [],
        },
    )
    monkeypatch.setattr(
        profile_discovery_pipeline,
        "_profile_advance_pipeline_status",
        lambda *_args: "partial",
    )

    result = asyncio.run(
        profile_discovery_pipeline.execute_smart_search_profile_advance_pipeline(
            session_id=78,
            payload={
                "query_text": "美国 YouTube 镜头评测创作者",
                "include_new_discovery": False,
                "include_content_fit": False,
                "include_lazy_video_backfill": False,
            },
        )
    )

    assert captured["query_text"] == "lens review photographer"
    assert result["query"] == "lens review photographer"
    assert result["query_plan_source"] == "llm_plan_with_guard_anchors"
