from __future__ import annotations

from app.platform.apify_budget import apify_execution_context, current_apify_execution_context
from app.services.scraping import apify as apify_scraper
from app.workers import apify_jobs_worker_media as media


def test_instagram_media_resolution_keeps_durable_fence_in_parent_process(monkeypatch):
    observed: list[tuple[str, int] | None] = []

    async def fake_scrape(url: str, platform: str, *, timeout_secs: int | None = None):
        observed.append(current_apify_execution_context())
        assert url == "https://www.instagram.com/reel/abc123/"
        assert platform == "instagram"
        assert timeout_secs == media.MEDIA_RESOLVE_TIMEOUT_SECONDS
        return {
            "scraped_ok": True,
            "video_url": "https://cdn.example.test/reel.mp4",
        }

    monkeypatch.setenv("APIFY_TOKEN", "configured-for-test")
    monkeypatch.setattr(apify_scraper, "scrape_with_apify", fake_scrape)
    monkeypatch.setattr(
        media.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("media resolver must not spawn a child")),
    )

    with apify_execution_context("apify-job:123", 17):
        result = media._resolve_video_media({"content_url": "https://www.instagram.com/reel/abc123/"})

    assert observed == [("apify-job:123", 17)]
    assert result["ok"] is True
    assert result["direct_video_url"] == "https://cdn.example.test/reel.mp4"
    assert "durable_execution_context_required" not in str(result)


def test_media_resolution_without_worker_fence_fails_closed_without_provider_call(monkeypatch):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("provider adapter must not run without a durable fence")

    monkeypatch.setenv("APIFY_TOKEN", "configured-for-test")
    monkeypatch.setattr(apify_scraper, "scrape_with_apify", forbidden)

    result = media._resolve_video_media({"content_url": "https://www.tiktok.com/@creator/video/123"})

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert "durable_execution_context_required" in result["reason"]
