from __future__ import annotations

from app.services.vkpi.official_post_identity import canonical_post_identity


def test_youtube_identity_prefers_video_id_and_canonical_watch_url():
    identity = canonical_post_identity(
        "youtube",
        {"id": {"videoId": "abc12345678"}, "url": "https://youtu.be/abc12345678?si=tracking"},
    )

    assert identity["platform"] == "youtube"
    assert identity["provider_post_id"] == "abc12345678"
    assert identity["canonical_post_uid"] == "youtube:abc12345678"
    assert identity["canonical_url"] == "https://www.youtube.com/watch?v=abc12345678"
    assert identity["post_identity_source"] == "provider_post_id"


def test_instagram_identity_extracts_shortcode_from_reel_url():
    identity = canonical_post_identity("instagram", {"url": "https://www.instagram.com/reel/CODE123/?igsh=tracking"})

    assert identity["provider_post_id"] == "CODE123"
    assert identity["canonical_post_uid"] == "instagram:CODE123"


def test_tiktok_identity_extracts_video_id_from_url():
    identity = canonical_post_identity(
        "tiktok",
        {"webVideoUrl": "https://www.tiktok.com/@viltrox.global/video/7642560794273664277?lang=en"},
    )

    assert identity["provider_post_id"] == "7642560794273664277"
    assert identity["canonical_post_uid"] == "tiktok:7642560794273664277"


def test_facebook_identity_extracts_story_fbid_query():
    identity = canonical_post_identity(
        "facebook",
        {"url": "https://www.facebook.com/story.php?story_fbid=123456789&id=987654321"},
    )

    assert identity["provider_post_id"] == "123456789"
    assert identity["canonical_post_uid"] == "facebook:123456789"


def test_reddit_identity_strips_t3_prefix():
    prefixed = canonical_post_identity("reddit", {"name": "t3_abc123"})
    from_url = canonical_post_identity("reddit", {"url": "https://www.reddit.com/r/lenses/comments/abc123/title/"})

    assert prefixed["provider_post_id"] == "abc123"
    assert prefixed["canonical_post_uid"] == "reddit:abc123"
    assert from_url["provider_post_id"] == "abc123"


def test_x_identity_extracts_status_id_and_normalizes_domain():
    identity = canonical_post_identity("twitter", {"url": "https://twitter.com/viltrox/status/1888888888888888888?s=20"})

    assert identity["platform"] == "x"
    assert identity["provider_post_id"] == "1888888888888888888"
    assert identity["canonical_post_uid"] == "x:1888888888888888888"
    assert identity["canonical_url"] == "https://x.com/viltrox/status/1888888888888888888"


def test_identity_falls_back_to_url_hash_when_provider_id_missing():
    identity = canonical_post_identity("website", {"url": "https://example.com/posts/a?utm_source=x"})

    assert identity["provider_post_id"] == ""
    assert identity["canonical_post_uid"].startswith("website:url:")
    assert identity["canonical_url"] == "https://example.com/posts/a"
    assert identity["post_identity_source"] == "canonical_url"
