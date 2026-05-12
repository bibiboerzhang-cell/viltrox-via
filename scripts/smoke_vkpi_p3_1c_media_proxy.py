#!/usr/bin/env python3
"""Offline smoke for P3.1C media proxy hardening.

This smoke is intentionally offline. It validates the allowlist, range-header
sanitization, and route registration without touching Instagram/TikTok/CDN
network URLs or spending crawler budget.
"""
from __future__ import annotations

from fastapi import HTTPException

from app.api.routers import media


def _must_reject(url: str) -> None:
    try:
        media._allowed_external_video_url(url)
    except HTTPException:
        return
    raise AssertionError(f"expected rejection for {url}")


def main() -> None:
    instagram_url, instagram_host = media._allowed_external_video_url(
        "https://scontent.cdninstagram.com/o1/v/t16/f2/m82/test.mp4"
    )
    assert instagram_url.startswith("https://")
    assert instagram_host.endswith("cdninstagram.com")

    tiktok_url, tiktok_host = media._allowed_external_video_url(
        "https://v16-webapp-prime.tiktokcdn-us.com/video/tos/useast2a/test.mp4"
    )
    assert tiktok_url.startswith("https://")
    assert tiktok_host.endswith("tiktokcdn-us.com")

    assert media._safe_range_header("bytes=0-1023") == "bytes=0-1023"
    assert media._safe_range_header("bytes=1024-") == "bytes=1024-"
    assert media._safe_range_header("bytes=-2048") == "bytes=-2048"
    assert media._safe_range_header("items=0-1") == ""
    assert media._safe_range_header("bytes=0-1\r\nX-Bad: 1") == ""

    headers = media._upstream_video_headers("scontent.cdninstagram.com", "bytes=0-1023")
    assert headers["Range"] == "bytes=0-1023"
    assert "User-Agent" in headers
    assert headers["Referer"].startswith("https://")

    _must_reject("https://example.com/video.mp4")
    _must_reject("file:///tmp/video.mp4")
    _must_reject("javascript:alert(1)")

    paths = {getattr(route, "path", "") for route in media.router.routes}
    assert "/api/admin/vkpi/media/image-proxy" in paths
    assert "/api/admin/vkpi/media/video-proxy" in paths
    assert "/api/admin/vkpi/media/video-redirect" in paths

    print("VKPI_P3_1C_MEDIA_PROXY_SMOKE_OK")


if __name__ == "__main__":
    main()
