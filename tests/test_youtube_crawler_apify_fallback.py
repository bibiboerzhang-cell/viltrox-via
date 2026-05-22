from __future__ import annotations

from io import BytesIO
from urllib.error import HTTPError

from app.services.vkpi.industry_crawlers.youtube_crawler import YouTubeCrawler


def _quota_payload() -> dict[str, object]:
    return {
        "provider": "youtube",
        "provider_status": "quota_exceeded",
        "sync_status": "quota_exceeded",
        "items": [],
        "error_reason": "quotaExceeded",
        "http_status": 403,
    }


def _actor_item() -> dict[str, object]:
    return {
        "title": "Sample video",
        "url": "https://www.youtube.com/watch?v=abc12345678",
        "thumbnailUrl": "https://i.ytimg.com/vi/abc12345678/hqdefault.jpg",
        "viewCount": 1200,
        "likes": 50,
        "commentsCount": 7,
        "date": "2026-05-01T00:00:00Z",
        "duration": "PT1M30S",
        "channelName": "Lens Channel",
        "channelUrl": "https://www.youtube.com/@lens",
        "numberOfSubscribers": 34500,
    }


def _clear_provider_env(monkeypatch) -> None:
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_YOUTUBE_API_KEY", raising=False)
    monkeypatch.delenv("APIFY_TOKEN", raising=False)


def test_http_403_quota_reason_is_preserved() -> None:
    body = b'{"error":{"code":403,"errors":[{"reason":"quotaExceeded"}],"message":"quota used"}}'
    exc = HTTPError("https://example.invalid", 403, "Forbidden", {}, BytesIO(body))
    result = YouTubeCrawler(api_key="yt")._http_error_payload(exc)

    assert result["provider_status"] == "quota_exceeded"
    assert result["sync_status"] == "quota_exceeded"
    assert result["error_reason"] == "quotaExceeded"
    assert result["http_status"] == 403


def test_channel_profile_falls_back_to_apify_when_youtube_quota_is_exceeded(monkeypatch) -> None:
    crawler = YouTubeCrawler(api_key="yt", apify_token="apify")
    captured: dict[str, object] = {}

    monkeypatch.setattr(crawler, "_request", lambda *_args, **_kwargs: _quota_payload())

    def fake_start(input_payload: dict[str, object], *, operation: str) -> dict[str, object]:
        captured["operation"] = operation
        captured["input"] = input_payload
        return {
            "provider": "youtube",
            "provider_status": "ok",
            "sync_status": "synced",
            "items": [_actor_item()],
            "raw": {"actor_id": "streamers/youtube-scraper", "input": input_payload},
        }

    monkeypatch.setattr(crawler, "_start_apify_run", fake_start)

    result = crawler.crawl_channel_profile("@lens", max_posts=1)

    assert captured["operation"] == "crawl_channel_profile"
    assert captured["input"] == {
        "startUrls": [{"url": "https://www.youtube.com/@lens/videos"}],
        "maxResults": 1,
        "maxResultsShorts": 0,
        "maxResultStreams": 0,
    }
    assert result["provider_source"] == "apify"
    assert result["fallback_from"] == "youtube_api"
    assert result["sync_status"] == "synced"
    assert result["items"][0]["statistics"]["subscriberCount"] == 34500
    assert result["videos"][0]["statistics"]["viewCount"] == 1200


def test_channel_videos_falls_back_to_apify_when_api_key_is_missing(monkeypatch) -> None:
    _clear_provider_env(monkeypatch)
    crawler = YouTubeCrawler(api_key="", apify_token="apify")

    monkeypatch.setattr(
        crawler,
        "_start_apify_run",
        lambda input_payload, *, operation: {
            "provider": "youtube",
            "provider_status": "ok",
            "sync_status": "synced",
            "items": [_actor_item()],
            "raw": {"actor_id": "streamers/youtube-scraper", "input": input_payload},
        },
    )

    result = crawler.crawl_channel_videos("@lens", max_results=1)

    assert result["provider_source"] == "apify"
    assert result["fallback_from"] == "youtube_api_not_configured"
    assert result["items"][0]["id"] == "abc12345678"


def test_quota_exceeded_stays_explicit_when_apify_is_not_configured(monkeypatch) -> None:
    _clear_provider_env(monkeypatch)
    crawler = YouTubeCrawler(api_key="yt", apify_token="")
    monkeypatch.setattr(crawler, "_request", lambda *_args, **_kwargs: _quota_payload())

    result = crawler.crawl_channel_profile("@lens", max_posts=1)

    assert result["provider_status"] == "quota_exceeded"
    assert result["sync_status"] == "quota_exceeded"
    assert result["apify_fallback_status"] == "not_configured"
