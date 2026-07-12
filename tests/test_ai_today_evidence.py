from __future__ import annotations

import json
import logging

from app.domains.market import ai_today
from app.domains.market.ai_today import (
    VILTROX_TOKEN,
    _extract_grounding_sources,
    _freshness_payload,
    _market_sources,
    _platform_video_id,
    _rank_video_candidates,
    _read_hot_brands,
    _recommended_video_rows,
)


def test_extract_grounding_sources_dedupes_real_urls() -> None:
    response = {
        "candidates": [
            {
                "groundingMetadata": {
                    "groundingChunks": [
                        {"web": {"uri": "https://example.com/a", "title": "Source A"}},
                        {"web": {"uri": "https://example.com/a", "title": "Duplicate"}},
                        {"web": {"uri": "not-a-url", "title": "Invalid"}},
                        {"web": {"uri": "https://example.org/b", "title": "Source B"}},
                    ]
                }
            }
        ]
    }

    assert _extract_grounding_sources(response) == [
        {"title": "Source A", "url": "https://example.com/a", "provider": "google_search", "relation_type": "grounding"},
        {"title": "Source B", "url": "https://example.org/b", "provider": "google_search", "relation_type": "grounding"},
    ]


def test_platform_video_id_supports_real_platform_urls() -> None:
    assert _platform_video_id("youtube", "https://www.youtube.com/watch?v=abc123") == "abc123"
    assert _platform_video_id("youtube", "https://youtu.be/short456?t=2") == "short456"
    assert _platform_video_id("tiktok", "https://www.tiktok.com/@creator/video/987654321") == "987654321"
    assert _platform_video_id("instagram", "https://www.instagram.com/reel/DemoCode/") == "DemoCode"


def test_rank_video_candidates_uses_analysis_media_and_creator_diversity() -> None:
    rows = [
        {
            "evidence_id": 11,
            "kol_pool_id": 1,
            "platform": "youtube",
            "content_url": "https://www.youtube.com/watch?v=night01",
            "title": "Cinematic night street test",
            "thumbnail_url": "https://img.example/night.jpg",
            "view_count": 12000,
            "handle": "creator_one",
            "display_name": "Creator One",
            "viltrox_fit_score": 88,
            "deep_result_id": 101,
            "analysis_cache_id": 201,
            "analysis_result": {
                "raw_gemini_video": {
                    "content_summary": "城市夜景与弱光街拍",
                    "marketing_notes": "匹配今日的电影感弱光方向",
                }
            },
        },
        {
            "evidence_id": 12,
            "kol_pool_id": 1,
            "platform": "youtube",
            "content_url": "https://www.youtube.com/watch?v=night02",
            "title": "Another night video from the same creator",
            "thumbnail_url": "https://img.example/night-2.jpg",
            "view_count": 50000,
            "handle": "creator_one",
            "viltrox_fit_score": 95,
            "deep_result_id": 102,
            "analysis_cache_id": 202,
            "analysis_result": {"raw_gemini_video": {"content_summary": "night city"}},
        },
        {
            "evidence_id": 13,
            "kol_pool_id": 2,
            "platform": "instagram",
            "content_url": "https://www.instagram.com/reel/portrait01/",
            "title": "Low-light portrait and bokeh",
            "thumbnail_url": "https://img.example/portrait.jpg",
            "view_count": 8000,
            "handle": "creator_two",
            "viltrox_fit_score": 84,
            "deep_result_id": 103,
            "analysis_cache_id": 203,
            "analysis_result": {"raw_gemini_video": {"content_summary": "弱光人像与大光圈虚化"}},
        },
        {
            "evidence_id": 14,
            "kol_pool_id": 3,
            "platform": "tiktok",
            "content_url": "https://www.tiktok.com/@three/video/123456789",
            "title": "Travel creator setup",
            "cached_video_url": "/api/vkpi-media/video-cache/demo",
            "video_storage_backend": "r2",
            "video_r2_key": "vkpi-media/videos/demo.mp4",
            "view_count": 900,
            "handle": "creator_three",
            "viltrox_fit_score": 70,
            "deep_result_id": 104,
            "analysis_cache_id": 204,
            "analysis_result": {"raw_gemini_video": {"content_summary": "compact creator camera"}},
        },
        {
            "evidence_id": 15,
            "kol_pool_id": 4,
            "platform": "youtube",
            "content_url": "https://www.youtube.com/watch?v=four",
            "title": "General camera review",
            "thumbnail_url": "https://img.example/four.jpg",
            "view_count": 400,
            "handle": "creator_four",
            "viltrox_fit_score": 65,
            "deep_result_id": 105,
            "analysis_cache_id": 205,
            "analysis_result": {"raw_gemini_video": {"content_summary": "camera review"}},
        },
    ]

    result = _rank_video_candidates(
        rows,
        {
            "headline": "今日主打电影感城市夜景",
            "shooting_plans": ["弱光街拍与大光圈人像"],
            "hot_topics": ["cinematic low light"],
        },
    )

    assert result[0]["evidence_id"] == 11
    assert len(result) == 4
    assert len({item["kol_pool_id"] for item in result}) == 4
    assert result[0]["platform_video_id"] == "night01"
    assert result[0]["source_refs"] == [
        {"table": "vkpi_kol_video_evidence", "id": 11},
        {"table": "vkpi_analysis_cache", "id": 201},
        {"table": "vkpi_kol_llm_deep_analysis_results", "id": 101},
    ]
    r2_item = next(item for item in result if item["evidence_id"] == 14)
    assert r2_item["playback_url"] == "/api/vkpi-media/video-cache/demo"
    assert r2_item["playback_source"] == "r2"
    assert all(item["content_origin"] == "external" for item in result)


def test_rank_video_candidates_excludes_owned_and_unknown_publishers() -> None:
    rows = [
        {
            "evidence_id": 1,
            "kol_pool_id": 1,
            "platform": "instagram",
            "content_url": "https://www.instagram.com/reel/owned/",
            "channel_name": "viltrox.official",
            "handle": "some_pool_row",
            "title": "Owned post",
            "thumbnail_url": "https://img.example/owned.jpg",
        },
        {
            "evidence_id": 2,
            "kol_pool_id": 2,
            "platform": "youtube",
            "content_url": "https://www.youtube.com/watch?v=unknown",
            "title": "Unknown publisher",
            "thumbnail_url": "https://img.example/unknown.jpg",
        },
        {
            "evidence_id": 3,
            "kol_pool_id": 3,
            "platform": "tiktok",
            "content_url": "https://www.tiktok.com/@outside_creator/video/123",
            "title": "External creator",
            "thumbnail_url": "https://img.example/external.jpg",
        },
    ]

    result = _rank_video_candidates(rows, {"headline": "camera video"})

    assert [item["evidence_id"] for item in result] == [3]
    assert result[0]["content_origin"] == "external"


def test_freshness_payload_marks_old_snapshot_stale() -> None:
    freshness = _freshness_payload("2020-01-01T00:00:00Z")
    assert freshness["freshness_status"] == "stale"
    assert freshness["age_hours"] > 36


class _CaptureResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, object]]:
        return self._rows

    def fetchone(self) -> dict[str, object] | None:
        return self._rows[0] if self._rows else None


class _CaptureConn:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self.queries: list[str] = []
        self.params: list[tuple[object, ...]] = []
        self._rows = rows or []

    def execute(self, query: str, params: tuple[object, ...] = ()) -> _CaptureResult:
        self.queries.append(query)
        self.params.append(tuple(params))
        return _CaptureResult(self._rows)


def test_recommended_video_rows_excludes_own_content_at_query_layer(monkeypatch) -> None:
    """「AI Today 只看外部世界」:三类自家内容(标题带品/官号帖/合作产出)在采样 SQL 层排除。"""
    conn = _CaptureConn()
    monkeypatch.setattr(ai_today, "get_conn", lambda: conn)
    monkeypatch.setattr(
        ai_today.logger, "isEnabledFor", lambda _level: False
    )

    assert _recommended_video_rows(limit=100) == []

    assert len(conn.queries) == 1
    query = conn.queries[0]
    assert (
        "AND NOT (strpos(lower(COALESCE(e.video_title, '') || ' ' || COALESCE(e.title, '')), ?) > 0)"
        in query
    )
    assert "AND NOT (e.project_id IS NOT NULL)" in query
    assert "vkpi_employee_channels" in query
    assert conn.params[0] == (VILTROX_TOKEN, 100)


def test_recommended_video_rows_logs_excluded_counts_at_debug(monkeypatch) -> None:
    count_row = {
        "pool_total": 483,
        "own_title_mention": 157,
        "own_project_linked": 294,
        "own_official_channel": 0,
        "own_excluded_total": 405,
    }
    conn = _CaptureConn(rows=[count_row])
    monkeypatch.setattr(ai_today, "get_conn", lambda: conn)
    monkeypatch.setattr(
        ai_today.logger, "isEnabledFor", lambda level: level == logging.DEBUG
    )

    _recommended_video_rows(limit=50)

    assert len(conn.queries) == 2
    count_query = conn.queries[0]
    assert "own_excluded_total" in count_query
    assert "vkpi_employee_channels" in count_query
    assert conn.params[0] == (VILTROX_TOKEN, VILTROX_TOKEN)


def test_read_hot_brands_drops_own_brand(tmp_path) -> None:
    payload = {"hot_brands": ["Viltrox", "Sony", "viltrox AF 85mm", "Sigma"]}
    (tmp_path / "20260712-market-signal.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    assert _read_hot_brands(ops_dir=str(tmp_path)) == ["Sony", "Sigma"]


def test_market_sources_exclude_own_brand_in_query(monkeypatch) -> None:
    conn = _CaptureConn()
    monkeypatch.setattr(ai_today, "get_conn", lambda: conn)

    assert _market_sources(["Sony"]) == []

    assert len(conn.queries) == 2
    for query, params in zip(conn.queries, conn.params):
        assert "strpos(lower(COALESCE(" in query
        assert ", ?) = 0" in query
        assert params == (VILTROX_TOKEN,)
