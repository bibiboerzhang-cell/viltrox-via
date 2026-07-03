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
  - reddit        (PRAW + Apify fallback) ← P1.1
  - facebook      (Apify + Meta Graph reserved) ← P1.2

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
from .reddit_crawler import RedditCrawler
from .facebook_crawler import FacebookCrawler

from app.core.logging import get_logger

logger = get_logger(__name__)


def record_apify_run_cost(
    run: Any,
    *,
    platform: str,
    actor_id: str = "",
    operation: str = "",
) -> None:
    """护栏④ 记账可见:Apify run 记账,C5 起统一走 budget_guard.record_apify_run 收口。

    收口后本函数只是薄转发(保留旧签名兜住全部既有调用点):幂等(同 run_id 不双记)、
    usageTotalUsd + 付费 actor 估算费都在统一口里。统一口不可用(极端部署错位)时
    回退旧的 usageTotalUsd 直记,保证记账能力不倒退。
    纯记账 / 不预检 / 不拦截;记账异常绝不打断爬取。
    """
    try:
        from app.domains.costs.budget_guard import record_apify_run

        record_apify_run(
            run,
            actor_id=str(actor_id or ""),
            platform=str(platform or ""),
            operation=str(operation or ""),
            source="industry_crawlers",
        )
        return
    except Exception:
        logger.warning("unified apify cost entry unavailable, falling back to legacy record", exc_info=True)
    # ── 旧路径回退(与收口前逐字同款):只取 usageTotalUsd 直落账本。──
    try:
        if not isinstance(run, dict):
            return
        cost = run.get("usageTotalUsd")
        if cost is None:
            usage = run.get("usageUsd")
            if isinstance(usage, dict):
                cost = sum(
                    float(value or 0)
                    for value in usage.values()
                    if isinstance(value, (int, float))
                )
        try:
            cost_usd = float(cost or 0)
        except (TypeError, ValueError):
            cost_usd = 0.0
        from app.domains.costs.budget_guard import record_cost

        record_cost(
            scope="provider:apify",
            ai_provider="apify",
            model_name=str(actor_id or ""),
            cost_usd=cost_usd,
            extra_scopes=["monthly_total"],
            metadata={
                "platform": platform,
                "actor_id": str(actor_id or ""),
                "operation": operation,
                "apify_run_id": run.get("id"),
                "run_status": run.get("status"),
                "usage_total_usd": cost_usd,
            },
        )
    except Exception:
        # 记账失败绝不阻断爬取。
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        return


_CRAWLER_REGISTRY: dict[str, type[Any]] = {
    "youtube": YouTubeCrawler,
    "instagram": InstagramCrawler,
    "tiktok": TikTokCrawler,
    "xiaohongshu": XiaohongshuCrawler,
    "bilibili": BilibiliCrawler,
    "x": XCrawler,
    "twitch": TwitchCrawler,
    "reddit": RedditCrawler,
    "facebook": FacebookCrawler,
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
    "RedditCrawler",
    "FacebookCrawler",
    "get_crawler",
    "supported_platforms",
    "is_supported",
]
