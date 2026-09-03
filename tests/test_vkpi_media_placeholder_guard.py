"""毒缓存防线 + 播放数诚实口径(2026-07-12)。

背景:image-proxy 曾把上游失败(签名过期/网络不可达)时的 1x1 透明 SVG 写进磁盘缓存,
之后 cached_image_url() 把这份失败占位当"已缓存真图"上报(media_status=cached),
公开端点以 200 image/svg+xml 返回一张看不见的假图 → 前端渲染成空白渐变占位。
同时 snapshot_sample 回退路径从不写 views_unavailable → IG 图文帖显示「播放 0」撒谎。
"""
from __future__ import annotations

import urllib.parse

from app.api.routers import media as media_router
from app.domains.media import cache_core as media_cache_core
from app.domains.channels import posts as channel_posts_module
from app.domains.media import cache as media_cache

TRANSPARENT_SVG = media_router._TRANSPARENT_IMAGE_SVG
REAL_JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"0" * 900  # 假 JPEG 头,只要不是小 SVG 即视为真图


def _write_entry(tmp_path, digest: str, data: bytes, content_type: str):
    cache_path = tmp_path / digest
    content_type_path = tmp_path / f"{digest}.content-type"
    cache_path.write_bytes(data)
    content_type_path.write_text(content_type)
    return cache_path, content_type_path


def test_placeholder_detector_flags_tiny_svg(tmp_path):
    cache_path, content_type_path = _write_entry(tmp_path, "a" * 64, TRANSPARENT_SVG, "image/svg+xml")
    assert media_cache.is_failure_placeholder_cache(cache_path, content_type_path) is True


def test_placeholder_detector_keeps_real_image(tmp_path):
    cache_path, content_type_path = _write_entry(tmp_path, "b" * 64, REAL_JPEG_BYTES, "image/jpeg")
    assert media_cache.is_failure_placeholder_cache(cache_path, content_type_path) is False


def test_cached_image_file_treats_placeholder_as_miss(tmp_path, monkeypatch):
    monkeypatch.setattr(media_cache, "CACHE_DIR", tmp_path)
    poisoned = "c" * 64
    real = "d" * 64
    _write_entry(tmp_path, poisoned, TRANSPARENT_SVG, "image/svg+xml")
    _write_entry(tmp_path, real, REAL_JPEG_BYTES, "image/jpeg")

    assert media_cache.cached_image_file(poisoned) is None  # 毒缓存 → 404,不再撒谎
    found = media_cache.cached_image_file(real)
    assert found is not None and found[1] == "image/jpeg"


def test_cached_image_url_skips_placeholder(tmp_path, monkeypatch):
    url = "https://scontent.cdninstagram.com/poisoned.jpg"
    digest, cache_path, content_type_path = media_cache._cache_paths(url)
    monkeypatch.setattr(media_cache, "_cache_paths", lambda _url, **_kwargs: (digest, tmp_path / digest, tmp_path / f"{digest}.content-type"))
    _write_entry(tmp_path, digest, TRANSPARENT_SVG, "image/svg+xml")

    # 存在但是失败占位 → 不得声称已缓存(media_status=cached 的上游谎言就来自这里)。
    assert media_cache.cached_image_url(url) == ""


def test_cached_image_url_miss_does_not_create_cache_directory(tmp_path, monkeypatch):
    missing = tmp_path / "not-created"
    monkeypatch.setattr(media_cache_core, "CACHE_DIR", missing)

    assert media_cache.cached_image_url("https://scontent.cdninstagram.com/missing.jpg") == ""
    assert not missing.exists()


def test_fetch_external_image_failure_reports_not_ok(monkeypatch):
    def _boom(*args, **kwargs):
        raise OSError("network unreachable")

    # S-03(2026-09-02):image-proxy 出站改走 app.platform.safe_fetch,mock 缝随之搬到 open_url。
    monkeypatch.setattr(media_router.safe_fetch, "open_url", _boom)
    data, content_type, ok = media_router._fetch_external_image("https://scontent.cdninstagram.com/x.jpg", "scontent.cdninstagram.com")
    assert ok is False
    assert data == TRANSPARENT_SVG
    assert content_type == "image/svg+xml"


def test_eu_tiktok_media_host_is_explicitly_allowlisted_for_same_origin_proxy():
    url = "https://p16-sign-va.tiktokcdn-eu.com/tos-maliva-avt-0068/avatar.jpeg"

    normalized, host = media_router._allowed_external_image_url(url)

    assert normalized == url
    assert host == "p16-sign-va.tiktokcdn-eu.com"


def test_both_youtube_thumbnail_hosts_are_explicitly_allowlisted_for_same_origin_proxy():
    for url in (
        "https://i.ytimg.com/vi/abc12345678/maxresdefault.jpg",
        "https://img.youtube.com/vi/abc12345678/hqdefault.jpg",
    ):
        normalized, host = media_router._allowed_external_image_url(url)

        assert normalized == url
        assert host in {"i.ytimg.com", "img.youtube.com"}


def test_both_youtube_thumbnail_hosts_are_allowlisted_for_cache_warmup():
    for url in (
        "https://i.ytimg.com/vi/abc12345678/maxresdefault.jpg",
        "https://img.youtube.com/vi/abc12345678/hqdefault.jpg",
    ):
        assert media_cache_core._normalize_image_url(url) == (
            url,
            urllib.parse.urlparse(url).hostname,
        )


def test_youtube_profile_avatar_hosts_are_allowlisted_for_proxy_and_cache_warmup():
    for url in (
        "https://yt3.ggpht.com/profile-avatar",
        "https://yt3.googleusercontent.com/profile-avatar",
    ):
        normalized, host = media_router._allowed_external_image_url(url)
        assert normalized == url
        assert host == urllib.parse.urlparse(url).hostname
        assert media_cache_core._normalize_image_url(url) == (url, host)


# --- 播放数诚实口径 ---

def test_instagram_image_post_views_marked_unavailable():
    post = {"platform": "instagram", "media_kind": "image", "views": 0}
    channel_posts_module._apply_views_semantics(post)
    assert post["views_unavailable"] is True
    assert post["views_unavailable_reason"]


def test_instagram_carousel_zero_views_marked_unavailable_snapshot_path():
    # snapshot_sample 路径的帖子没有 views_unavailable 字段 → 补口径后必须为 True。
    post = {"media_kind": "carousel", "views": 0}
    channel_posts_module._apply_views_semantics(post, fallback_platform="instagram")
    assert post["views_unavailable"] is True


def test_instagram_video_zero_views_stays_available():
    # 视频帖 0 = 实测零,不是「无口径」;与 NULL(views_missing)区分。
    post = {"platform": "instagram", "media_kind": "video", "views": 0}
    channel_posts_module._apply_views_semantics(post)
    assert post["views_unavailable"] is False


def test_reddit_post_views_always_unavailable():
    post = {"platform": "reddit", "media_kind": "image", "views": 123}
    channel_posts_module._apply_views_semantics(post)
    assert post["views_unavailable"] is True


def test_package_row_distinguishes_missing_views_from_zero():
    missing = channel_posts_module._post_from_package_row({"platform": "instagram", "url": "https://x/p/1", "views": ""})
    zero = channel_posts_module._post_from_package_row({"platform": "instagram", "url": "https://x/p/2", "views": "0"})
    assert missing["views_missing"] is True
    assert zero["views_missing"] is False
    assert zero["views"] == 0
