"""
services/scraping/platform_router.py — 平台抓取总入口
======================================================
Routing strategy:
  YouTube/Instagram/TikTok → Apify (primary) → Playwright (fallback)
  Reddit → reddit_json
  Bilibili/Xiaohongshu/Facebook → Playwright
"""
from __future__ import annotations

from typing import Any, Dict
from urllib.parse import urlparse

from app.core.logging import get_logger
from app.services.scraping.playwright_scraper import scrape_with_playwright
from app.services.scraping.reddit_json import scrape_reddit_json
from app.services.scraping.apify import scrape_with_apify, _apify_available

logger = get_logger(__name__)


def detect_platform_from_url(url: str) -> str:
    host = (urlparse(url).netloc or "").lower()
    if "tiktok.com" in host:
        return "TikTok"
    if "douyin.com" in host:
        return "Douyin"
    if "instagram.com" in host:
        return "Instagram"
    if "youtube.com" in host or "youtu.be" in host:
        return "YouTube"
    if "reddit.com" in host:
        return "Reddit"
    if "facebook.com" in host or "fb.watch" in host:
        return "Facebook"
    if "bilibili.com" in host:
        return "Bilibili"
    if "xiaohongshu.com" in host or "xhslink.com" in host:
        return "Xiaohongshu"
    return "Unknown"


async def scrape_url(url: str) -> Dict[str, Any]:
    """Unified scrape entry. Apify primary for IG/TT/YT, Playwright fallback."""
    platform = detect_platform_from_url(url)

    if platform == "Reddit":
        data = await scrape_reddit_json(url)
        data["platform"] = "Reddit"
        data.setdefault("source_url", url)
        return data

    APIFY_PLATFORMS = {"YouTube", "Instagram", "TikTok", "Douyin"}

    if platform in APIFY_PLATFORMS and _apify_available():
        logger.info("platform_router.apify_primary", extra={"platform": platform})
        try:
            data = await scrape_with_apify(url, platform)
            if data.get("scraped_ok"):
                data["platform"] = platform
                data.setdefault("source_url", url)
                logger.info("platform_router.apify_success", extra={"platform": platform, "views": data["metrics"].get("views", 0)})
                return data
            else:
                logger.warning("platform_router.apify_fallback", extra={"platform": platform, "error": data.get("error")})
        except Exception as e:
            logger.warning("platform_router.apify_exception", extra={"platform": platform, "error": str(e)})

    logger.info("platform_router.playwright_fallback", extra={"platform": platform})
    data = await scrape_with_playwright(url)
    data["platform"] = platform
    data.setdefault("source_url", url)
    return data
