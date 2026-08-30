from __future__ import annotations

import ast
from pathlib import Path

from app.services.scraping.apify_douyin_projection import project_douyin_result
from scripts.vkpi_engineering_health_collect import collect_complexity


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "backend/app/services/scraping/apify.py"
PROJECTION = ROOT / "backend/app/services/scraping/apify_douyin_projection.py"


def test_douyin_projection_preserves_public_result_contract() -> None:
    item = {
        "desc": "样例视频",
        "coverUrl": "https://media.example/cover.jpg",
        "create_time": 123,
        "unique_id": "creator-1",
        "nickname": "创作者",
        "avatarUrl": "https://media.example/avatar.jpg",
        "duration": 18,
        "hashtags": ["lens"],
    }
    result = project_douyin_result(
        item,
        views=10,
        likes=4,
        comments=3,
        shares=2,
        favorites=1,
        visible_comments=[{"text": "good"}],
        video_url="https://media.example/video.mp4",
        first_nested_int=lambda _item, keys: 99 if "followers" in keys else 88,
    )

    assert result["scraped_ok"] is True
    assert result["title"] == "样例视频"
    assert result["metrics"] == {
        "views": 10,
        "likes": 4,
        "comments": 3,
        "shares": 2,
        "favorites": 1,
    }
    assert all(result["metrics_available"].values())
    assert result["owner_username"] == "creator-1"
    assert result["owner_full_name"] == "创作者"
    assert result["channel_url"] == "https://www.douyin.com/user/creator-1"
    assert result["follower_count"] == 99
    assert result["total_favorited"] == 88
    assert result["scraper"] == "apify_douyin"


def test_douyin_adapter_family_stays_below_the_v1_complexity_redline() -> None:
    rows = []
    for source_path in (ADAPTER, PROJECTION):
        source = source_path.read_text(encoding="utf-8")
        rows.extend(collect_complexity({str(source_path): ast.parse(source)}))
        assert len(source.splitlines()) < 800
    scrape = next(row for row in rows if row.qualified_name == "scrape_douyin")

    assert scrape.cc <= 30
    assert max(row.cc for row in rows) <= 40
