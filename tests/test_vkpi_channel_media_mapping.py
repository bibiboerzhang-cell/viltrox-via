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


def test_media_contract_marks_youtube_as_embed():
    contract = channels._media_contract(
        "youtube",
        {"id": "abc12345678", "url": "https://www.youtube.com/watch?v=abc12345678"},
    )

    assert contract["media_status"] == "embed"
    assert contract["media_has_embed"] is True


def test_media_contract_keeps_video_inventory_distinct_from_cached_poster():
    contract = channels._media_contract(
        "instagram",
        {
            "id": "reel-1",
            "url": "https://www.instagram.com/reel/reel-1/",
            "media_kind": "video",
            "media_url": "/api/vkpi-media/image-cache/poster",
            "image_urls": ["/api/vkpi-media/image-cache/poster"],
        },
    )

    assert contract["media_status"] == "inventory_only"
    assert contract["media_cached_image_count"] == 1
    assert contract["media_has_cached_video"] is False


def test_media_contract_distinguishes_cached_and_source_only_images():
    cached = channels._media_contract(
        "facebook",
        {"media_url": "/api/vkpi-media/image-cache/photo", "media_kind": "image"},
    )
    source_only = channels._media_contract(
        "facebook",
        {"media_url": "https://scontent.xx.fbcdn.net/photo.jpg", "media_kind": "image"},
    )

    assert cached["media_status"] == "cached"
    assert source_only["media_status"] == "source_only"


def test_attach_post_identity_populates_shared_fields():
    posts = [{"id": "abc12345678", "url": "https://www.youtube.com/watch?v=abc12345678"}]

    channels._attach_post_identity(posts, "youtube")

    assert posts[0]["provider_post_id"] == "abc12345678"
    assert posts[0]["canonical_post_uid"] == "youtube:abc12345678"
    assert posts[0]["canonical_url"] == "https://www.youtube.com/watch?v=abc12345678"
    assert posts[0]["post_identity_source"] == "provider_post_id"
