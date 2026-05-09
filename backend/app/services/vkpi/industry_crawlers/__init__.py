"""Industry crawl adapters.

R-Phase2-B: 完整 7 平台 crawler registry

接口规范统一 (基于 R-Phase2-A 验证):
  - configured (property) -> bool
  - provider_status() -> dict
  - crawl_channel_profile(handle_or_url, *, channel_id, max_posts) -> dict
  - crawl_channel_videos(channel_id, *, max_results) -> dict
  - crawl_video_comments(video_id, *, max_results) -> dict (留 R-Phase3.2)
  - normalize_handle_ref(value) -> dict (静态方法,可选)

平台清单:
  - youtube       (YouTube Data API v3)
  - instagram     (Apify scraper)
  - tiktok        (Apify scraper)
  - xiaohongshu   (Apify scraper) ← R-Phase2-B
  - bilibili      (Apify scraper) ← R-Phase2-B
  - x             (X API + Apify fallback) ← R-Phase2-B
  - twitch        (Helix API) ← R-Phase2-B

未做 (留下次):
  - weibo         (Apify - 中国监管复杂)
  - youtube_shorts (扩展 youtube_crawler 标记 short)
"""
from __future__ import annotations

from typing import Any

from .youtube_crawler import YouTubeCrawler
from .instagram_crawler import InstagramCrawler
from .tiktok_crawler import TikTokCrawler
from .xiaohongshu_crawler import XiaohongshuCrawler
from .bilibili_crawler import BilibiliCrawler
from .x_crawler import XCrawler
from .twitch_crawler import TwitchCrawler


_CRAWLER_REGISTRY: dict[str, type[Any]] = {
    "youtube": YouTubeCrawler,
    "instagram": InstagramCrawler,
    "tiktok": TikTokCrawler,
    "xiaohongshu": XiaohongshuCrawler,
    "bilibili": BilibiliCrawler,
    "x": XCrawler,
    "twitch": TwitchCrawler,
}


def get_crawler(platform: str) -> Any | None:
    platform_key = str(platform or "").strip().lower()
    crawler_cls = _CRAWLER_REGISTRY.get(platform_key)
    if crawler_cls is None:
        return None
    return crawler_cls()


def supported_platforms() -> list[str]:
    return sorted(_CRAWLER_REGISTRY.keys())


def is_supported(platform: str) -> bool:
    return str(platform or "").strip().lower() in _CRAWLER_REGISTRY


__all__ = [
    "YouTubeCrawler",
    "InstagramCrawler",
    "TikTokCrawler",
    "XiaohongshuCrawler",
    "BilibiliCrawler",
    "XCrawler",
    "TwitchCrawler",
    "get_crawler",
    "supported_platforms",
    "is_supported",
]
