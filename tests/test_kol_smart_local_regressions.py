from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.domains.kol import (
    profile_recall_match_evidence,
    profile_recall_qualification,
    profile_discovery_pipeline,
    smart_query_planner,
)


def _item(item_id: int, *, handle: str) -> dict:
    return {
        "kol_pool_id": item_id,
        "handle": handle,
        "platform": "youtube",
        "bucket": "creator",
        "display_rank_score": 1.0,
        "recall_rank_score": 1.0,
        "match_evidence": [{"field": "bio", "term": "lens"}],
    }


def _row(item_id: int, *, followers: int) -> dict:
    return {
        "kol_pool_id": item_id,
        "followers": followers,
        "country": "US",
        "platform": "youtube",
        "raw_platform_data": {},
    }


def _evidence(posted_at: datetime) -> dict:
    return {
        "latest_real_video": {
            "posted_at": posted_at.isoformat(),
            "evidence_type": "video",
            "content_url": "https://example.test/video/latest",
            "source": "vkpi_kol_video_evidence.posted_at",
        }
    }


def test_controlled_lens_review_pair_is_evidence_but_generic_role_is_not() -> None:
    assert profile_recall_match_evidence.query_evidence_terms("camera creator") == []
    assert profile_recall_match_evidence.query_evidence_terms("lens review creator") == [
        "lens",
        "review",
    ]


def test_chinese_lens_review_plan_keeps_auditable_lexical_anchors() -> None:
    plan = smart_query_planner._fallback_plan("美国 YouTube 镜头评测创作者")
    assert "lens review" in plan["search_query"]
    assert profile_recall_match_evidence.query_evidence_terms(plan["search_query"])[:2] == [
        "lens",
        "review",
    ]


def test_exact_phrase_hit_is_extended_by_anchor_recall_to_requested_limit() -> None:
    class _Result:
        def __init__(self, rows: list[dict]) -> None:
            self._rows = rows

        def fetchall(self) -> list[dict]:
            return self._rows

    class _Conn:
        calls = 0

        def execute(self, _sql: str, _params: tuple) -> _Result:
            self.calls += 1
            if self.calls == 1:
                return _Result([{"kol_pool_id": 1}])
            return _Result([{"kol_pool_id": item_id} for item_id in range(1, 8)])

    ids = profile_recall_match_evidence.pool_text_fallback_ids(
        _Conn(),
        "lens review",
        5,
        max_candidate_limit=500,
        allow_backfill=False,
    )
    assert ids == [1, 2, 3, 4, 5]


def test_invalid_duplicate_does_not_hide_later_valid_account() -> None:
    now = datetime(2026, 8, 15, tzinfo=timezone.utc)
    invalid = _item(1, handle="same-account")
    valid = _item(2, handle="same-account")
    items, _, contract = profile_recall_qualification.qualify_local_candidates(
        buckets={"creator": [invalid, valid], "reviewer": []},
        rows_by_id={1: _row(1, followers=2_999), 2: _row(2, followers=5_000)},
        evidence_by_id={1: _evidence(now - timedelta(days=5)), 2: _evidence(now - timedelta(days=5))},
        policy=profile_recall_qualification.smart_local_policy(
            market="US", platforms=["youtube"]
        ),
        creator_quota=15,
        reviewer_quota=15,
        as_of=now,
    )
    assert [item["kol_pool_id"] for item in items] == [2]
    assert contract["rejected_by_reason"] == {"followers_below_3000": 1}
    funnel_values = [
        contract["funnel"][key]
        for key in (
            "canonical_unique",
            "followers_pass",
            "fresh_video_pass",
            "market_pass",
            "platform_pass",
            "qualified",
            "returned",
        )
    ]
    assert funnel_values == sorted(funnel_values, reverse=True)


def test_future_video_timestamp_fails_closed() -> None:
    now = datetime(2026, 8, 15, tzinfo=timezone.utc)
    candidate = _item(1, handle="future-account")
    items, _, contract = profile_recall_qualification.qualify_local_candidates(
        buckets={"creator": [candidate], "reviewer": []},
        rows_by_id={1: _row(1, followers=5_000)},
        evidence_by_id={1: _evidence(now + timedelta(days=365))},
        policy=profile_recall_qualification.smart_local_policy(
            market="US", platforms=["youtube"]
        ),
        creator_quota=15,
        reviewer_quota=15,
        as_of=now,
    )
    assert items == []
    assert contract["rejected_by_reason"] == {"latest_video_in_future": 1}
    assert candidate["qualification_evidence"]["activity"]["fresh_priority"] is False


def test_worker_keeps_provider_free_anchors_when_rich_plan_is_generic(monkeypatch) -> None:
    captured: dict = {}
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
            "search_query": "videographer photographer camera gear",
            "product_focus": ["camera gear"],
        },
    )
    monkeypatch.setattr(
        profile_discovery_pipeline.profile_recall,
        "recall_kol_profiles",
        lambda **kwargs: captured.update(kwargs) or {
            "items": [], "buckets": {"creator": [], "reviewer": []},
            "diagnostics": {"returned_count": 0},
            "local_qualification": {"returned_count": 0, "shortfall": 30},
        },
    )
    monkeypatch.setattr(profile_discovery_pipeline, "filter_recall_result_platforms", lambda result, _value: result)
    monkeypatch.setattr(profile_discovery_pipeline, "filter_recall_result_market", lambda result, _value: result)
    monkeypatch.setattr(profile_discovery_pipeline.search_sessions, "attach_recall_result", lambda *_args: {})
    monkeypatch.setattr(profile_discovery_pipeline.search_sessions, "update_session_result_summary", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        profile_discovery_pipeline,
        "advance_search_session_items",
        lambda **_kwargs: {
            "status": "empty", "selected": 0, "counts": {}, "items": [],
            "viltrox_fit_score_changed_ids": [],
        },
    )
    monkeypatch.setattr(profile_discovery_pipeline, "_profile_advance_pipeline_status", lambda *_args: "partial")

    result = asyncio.run(
        profile_discovery_pipeline.execute_smart_search_profile_advance_pipeline(
            session_id=77,
            payload={
                "query_text": "美国 YouTube 镜头评测创作者",
                "include_new_discovery": False,
                "include_content_fit": False,
                "include_lazy_video_backfill": False,
                "_smart_local_30_contract": True,
            },
        )
    )

    assert captured["query_text"] == "lens review photographer"
    assert result["query_plan_source"] == "llm_plan_with_guard_anchors"
