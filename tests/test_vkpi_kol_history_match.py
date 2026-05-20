from __future__ import annotations

import json

from app.db.connection import get_conn
from app.services.vkpi import kol_history_match
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema


MARKER = "vkpi-history-match-unit"


def _cleanup() -> None:
    conn = get_conn()
    conn.execute("DELETE FROM vkpi_kol_pool WHERE source_ref=?", (MARKER,))
    conn.commit()


def test_natural_history_search_includes_light_recent_posts():
    ensure_vkpi_product_industry_schema()
    _cleanup()
    conn = get_conn()
    now = "2026-05-20T10:00:00Z"
    raw = {
        "videos": [
            {
                "id": "video-1",
                "title": "Viltrox 35mm field test",
                "url": "https://example.com/video-1",
                "playCount": 1234,
                "diggCount": 56,
                "commentCount": 7,
                "publishedAt": "2026-05-19T10:00:00Z",
                "large_unused_field": "x" * 5000,
            }
        ]
    }
    try:
        conn.execute(
            """
            INSERT INTO vkpi_kol_pool
              (pool_uid, platform, handle, profile_url, display_name, avatar_url, bio, email,
               followers, following, posts_count, avg_views, avg_likes, avg_comments,
               engagement_rate, source_type, source_ref, raw_platform_data, created_by_staff_id,
               last_seen_at, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                f"{MARKER}-uid",
                "youtube",
                "historyrecent",
                "https://youtube.com/@historyrecent",
                "History Recent",
                "https://example.com/avatar.jpg",
                "Viltrox sample creator",
                "",
                1000,
                None,
                1,
                1234,
                56,
                7,
                0.063,
                "legacy_excel_p2d",
                MARKER,
                json.dumps(raw),
                None,
                now,
                now,
                now,
            ),
        )
        conn.commit()

        results = kol_history_match.search_pool_for_natural(
            "historyrecent",
            {"platform": "youtube", "keywords": ["historyrecent"]},
            limit=5,
        )

        assert results
        latest_posts = results[0]["latest_posts"]
        assert latest_posts == results[0]["posts"]
        assert latest_posts[0]["title"] == "Viltrox 35mm field test"
        assert latest_posts[0]["views"] == 1234
        assert "large_unused_field" not in latest_posts[0]
        assert results[0]["historical_match"]["recent_posts"][0]["post_url"] == "https://example.com/video-1"
    finally:
        _cleanup()


def test_platform_annotation_uses_history_recent_posts_when_provider_has_none():
    ensure_vkpi_product_industry_schema()
    _cleanup()
    conn = get_conn()
    now = "2026-05-20T10:00:00Z"
    try:
        conn.execute(
            """
            INSERT INTO vkpi_kol_pool
              (pool_uid, platform, handle, profile_url, display_name, avatar_url, bio, email,
               followers, following, posts_count, avg_views, avg_likes, avg_comments,
               engagement_rate, source_type, source_ref, raw_platform_data, created_by_staff_id,
               last_seen_at, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                f"{MARKER}-annotate-uid",
                "youtube",
                "historyannotate",
                "https://youtube.com/@historyannotate",
                "History Annotate",
                "",
                "",
                "",
                500,
                None,
                1,
                321,
                4,
                2,
                0.012,
                "legacy_excel_p2d",
                MARKER,
                json.dumps({"profile": {"posts": [{"id": "p1", "caption": "Recent cached post", "url": "https://example.com/p1"}]}}),
                None,
                now,
                now,
                now,
            ),
        )
        conn.commit()

        annotated = kol_history_match.annotate_platform_items(
            [{"platform": "youtube", "handle": "@historyannotate", "channel_name": "History Annotate"}],
            platform="youtube",
        )

        assert annotated[0]["historical_match"]["matched"] is True
        assert annotated[0]["latest_posts"][0]["title"] == "Recent cached post"
    finally:
        _cleanup()
