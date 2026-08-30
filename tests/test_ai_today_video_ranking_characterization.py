from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.domains.market import ai_today_evidence as evidence
from scripts.vkpi_engineering_health_collect import collect_complexity


ROOT = Path(__file__).resolve().parents[1]
RANKING_FAMILY = (
    ROOT / "backend/app/domains/market/ai_today_evidence.py",
    ROOT / "backend/app/domains/market/ai_today_video_ranking.py",
)


def _row(
    evidence_id: int,
    *,
    creator: str | None = None,
    title: str = "camera video",
    views: Any = 100,
    published_at: Any = None,
    analysis: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    handle = creator or f"creator_{evidence_id}"
    return {
        "evidence_id": evidence_id,
        "kol_pool_id": evidence_id,
        "platform": "youtube",
        "content_url": f"https://www.youtube.com/watch?v=video{evidence_id}",
        "title": title,
        "thumbnail_url": f"https://img.example/{evidence_id}.jpg",
        "view_count": views,
        "like_count": 0,
        "comment_count": 0,
        "publish_date": published_at,
        "handle": handle,
        "display_name": handle,
        "viltrox_fit_score": 50,
        "analysis_result": analysis or {},
        **extra,
    }


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz: Any = None) -> datetime:
        value = cls(2026, 1, 31, 12, 0, tzinfo=timezone.utc)
        return value if tz is None else value.astimezone(tz)


def test_rank_video_candidates_locks_freshness_views_and_engagement_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(evidence, "datetime", _FixedDateTime)
    rows = [
        _row(
            1,
            views=1_000,
            published_at="2025-11-02T12:00:00Z",
            like_count=1_000_000,
            comment_count=1_000_000,
        ),
        _row(2, views=1_000, published_at="2026-01-31T12:00:00Z"),
        _row(3, views=10_000, published_at=None),
    ]

    ranked = evidence._rank_video_candidates(rows, {"headline": "camera"})

    assert [item["evidence_id"] for item in ranked] == [3, 2, 1]
    assert ranked[1]["rank_score"] > ranked[2]["rank_score"]
    # Likes/comments are projected to the result but do not enter today's score.
    assert ranked[2]["like_count"] == 1_000_000
    assert ranked[2]["comment_count"] == 1_000_000


def test_rank_video_candidates_locks_content_evidence_and_owned_brand_boundary() -> None:
    rows = [
        _row(
            1,
            creator="viltrox.official",
            title="cinematic lens launch",
            analysis={"raw_gemini_video": {"viltrox_detected": "true"}},
        ),
        _row(
            2,
            title="generic clip",
            analysis={
                "raw_gemini_video": {
                    "content_summary": "cinematic anamorphic widescreen scene",
                    "viltrox_detected": "true",
                }
            },
        ),
        _row(
            3,
            title="generic clip",
            analysis={
                "raw_gemini_video": {
                    "content_summary": "cinematic anamorphic widescreen scene",
                    "viltrox_detected": "false",
                }
            },
        ),
    ]

    ranked = evidence._rank_video_candidates(
        rows,
        {"headline": "cinematic anamorphic film"},
    )

    assert [item["evidence_id"] for item in ranked] == [2, 3]
    assert ranked[0]["match_terms"] == ["anamorphic", "cinematic", "widescreen"]
    assert ranked[0]["rank_score"] - ranked[1]["rank_score"] == 8
    assert all(item["content_origin"] == "external" for item in ranked)


def test_rank_video_candidates_locks_media_threshold_and_best_creator_dedupe() -> None:
    rows = [
        _row(1, creator="same", views=50_000),
        _row(
            2,
            creator="same",
            views=100_000,
            content_url="not-a-public-url",
        ),
        _row(3, views=40_000),
        _row(4, views=30_000),
        _row(5, views=20_000, thumbnail_url=""),
    ]

    ranked = evidence._rank_video_candidates(
        rows,
        {"headline": "camera"},
        max_recommended_videos=4,
    )

    # Four media-bearing rows meet the threshold, so the no-media fifth row is
    # excluded. The invalid higher-scoring duplicate must not consume creator.
    assert [item["evidence_id"] for item in ranked] == [1, 3, 4]


def test_rank_video_candidates_locks_stable_tie_break_and_empty_values() -> None:
    first = _row(
        10,
        views=None,
        creator="first",
        title="",
        viltrox_fit_score=None,
        thumbnail_url="",
        cached_video_url="/api/vkpi-media/video-cache/first",
    )
    second = _row(
        11,
        views=None,
        creator="second",
        title="",
        viltrox_fit_score=None,
        thumbnail_url="",
        cached_video_url="/api/vkpi-media/video-cache/second",
    )

    ranked = evidence._rank_video_candidates(
        [first, second],
        {"headline": "unmapped brief"},
        max_recommended_videos=2,
    )

    assert [item["evidence_id"] for item in ranked] == [10, 11]
    assert ranked[0]["rank_score"] == ranked[1]["rank_score"]
    assert ranked[0]["title"] == "未命名视频"
    assert ranked[0]["why_recommended"] == "已完成视频深析"


def test_rank_video_candidates_preserves_malformed_fit_failure() -> None:
    row = _row(
        12,
        title="",
        views=None,
        viltrox_fit_score="",
        analysis={},
    )

    with pytest.raises(ValueError, match="could not convert string to float"):
        evidence._rank_video_candidates([row], {"headline": "unmapped brief"})


def test_rank_video_candidates_currently_ignores_operator_facets() -> None:
    rows = [
        _row(1, platform="youtube", market="US"),
        _row(2, platform="instagram", market="JP"),
    ]
    baseline = evidence._rank_video_candidates(rows, {"headline": "camera"})
    faceted = evidence._rank_video_candidates(
        rows,
        {
            "headline": "camera",
            "platform": "instagram",
            "market": "JP",
            "date_window_days": 7,
        },
    )

    assert [item["evidence_id"] for item in faceted] == [
        item["evidence_id"] for item in baseline
    ]


def test_ai_today_video_ranking_family_stays_bounded_and_leaf_directed() -> None:
    trees = {
        str(path): ast.parse(path.read_text(encoding="utf-8"))
        for path in RANKING_FAMILY
    }
    rows = collect_complexity(trees)
    facade = next(
        row
        for row in rows
        if row.path.endswith("ai_today_evidence.py")
        and row.qualified_name == "_rank_video_candidates"
    )

    assert facade.cc <= 10
    assert max(row.cc for row in rows) <= 30
    assert all(
        len(path.read_text(encoding="utf-8").splitlines()) < 800
        for path in RANKING_FAMILY
    )
    leaf_source = RANKING_FAMILY[1].read_text(encoding="utf-8")
    assert "from app." not in leaf_source
    assert "import app." not in leaf_source
