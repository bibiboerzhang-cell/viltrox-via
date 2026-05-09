import asyncio

from app.services.scraping.platform_router import detect_platform_from_url
from app.utils.urls import detect_platform


def test_douyin_urls_detect_as_douyin():
    assert detect_platform_from_url("https://v.douyin.com/HrJxM3rt-ZM/") == "Douyin"
    assert detect_platform("https://www.douyin.com/video/7633384417515441443") == "Douyin"


def test_douyin_apify_requires_configured_actor(monkeypatch):
    from app.services.scraping import apify

    monkeypatch.setattr(apify, "_client", object())
    for key in (
        "APIFY_DOUYIN_ACTOR_ID",
        "APIFY_DOUYIN_VIDEO_ACTOR_ID",
    ):
        monkeypatch.delenv(key, raising=False)

    result = asyncio.run(apify.scrape_douyin("https://v.douyin.com/HrJxM3rt-ZM/"))

    assert result["scraped_ok"] is False
    assert "APIFY_DOUYIN_VIDEO_ACTOR_ID" in result["error"]
