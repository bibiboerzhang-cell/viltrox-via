from __future__ import annotations

import json

from app.db.connection import get_conn
from app.domains.search import natural_search
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema


MARKER = "vkpi-natural-search-enrichment-unit"


def _cleanup() -> None:
    conn = get_conn()
    conn.execute("DELETE FROM vkpi_kol_pool WHERE source_ref=?", (MARKER,))
    conn.commit()


def test_natural_search_kol_pool_includes_avatar_and_recent_posts_without_raw_payload():
    ensure_vkpi_product_industry_schema()
    _cleanup()
    conn = get_conn()
    now = "2026-05-23T10:00:00Z"
    raw = {
        "profile": {
            "snippet": {
                "thumbnails": {
                    "high": {"url": "https://yt3.ggpht.com/natural-search-avatar=s800-c-k-c0x00ffffff-no-rj"}
                }
            }
        },
        "videos": [
            {
                "kind": "youtube#video",
                "id": "natural-search-video",
                "snippet": {
                    "title": "Natural Search Viltrox field test",
                    "publishedAt": "2026-05-22T10:00:00Z",
                },
                "statistics": {
                    "viewCount": "2234",
                    "likeCount": "65",
                    "commentCount": "8",
                },
                "large_unused_field": "x" * 5000,
            }
        ],
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
                "naturalsearchavatar",
                "https://youtube.com/@naturalsearchavatar",
                "Natural Search Avatar",
                "",
                "Viltrox sample creator for natural search",
                "",
                1200,
                None,
                1,
                2234,
                65,
                8,
                0.061,
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

        payload = natural_search.search("naturalsearchavatar", limit=5)
        result = next(item for item in payload["items"] if item.get("source_table") == "vkpi_kol_pool")

        assert payload["provider_calls"] is False
        assert payload["write_db"] is False
        assert result["avatar_url"] == "https://yt3.ggpht.com/natural-search-avatar=s800-c-k-c0x00ffffff-no-rj"
        assert result["recent_posts"][0]["post_url"] == "https://www.youtube.com/watch?v=natural-search-video"
        assert result["recent_posts"][0]["views"] == 2234
        assert "raw_platform_data" not in result["evidence"]
        assert "large_unused_field" not in json.dumps(result["evidence"], default=str)
    finally:
        _cleanup()
