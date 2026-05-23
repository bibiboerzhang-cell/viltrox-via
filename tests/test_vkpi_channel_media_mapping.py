from __future__ import annotations

from app.services.vkpi import channels


def test_media_urls_prefers_high_resolution_display_resource_when_cached(monkeypatch):
    high = "https://scontent.cdninstagram.com/high.jpg"
    low = "https://scontent.cdninstagram.com/low.jpg"
    monkeypatch.setattr(
        channels,
        "cached_image_url",
        lambda url: "/api/vkpi-media/image-cache/high" if url == high else "",
    )

    urls = channels._media_urls(
        [
            {"src": low, "config_width": 320, "config_height": 320},
            {"src": high, "config_width": 1080, "config_height": 1080},
        ]
    )

    assert urls[0] == "/api/vkpi-media/image-cache/high"
    assert high not in urls[:1]


def test_media_urls_keeps_cached_fallback_before_uncached_external_high_res(monkeypatch):
    high = "https://scontent.cdninstagram.com/high.jpg"
    low = "https://scontent.cdninstagram.com/low.jpg"
    monkeypatch.setattr(
        channels,
        "cached_image_url",
        lambda url: "/api/vkpi-media/image-cache/low" if url == low else "",
    )

    urls = channels._media_urls(
        [
            {"src": high, "config_width": 1080, "config_height": 1080},
            {"src": low, "config_width": 320, "config_height": 320},
        ]
    )

    assert urls[0] == "/api/vkpi-media/image-cache/low"
    assert high in urls
