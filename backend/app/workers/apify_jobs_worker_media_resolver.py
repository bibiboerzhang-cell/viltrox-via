"""Truthful provider-media resolution for the video worker.

This module keeps the network-result state machine separate from the already
large worker orchestration module.  The actual provider call is injected so
the parent worker still owns its timeout/process fence and tests can replace it
without reaching the network.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from app.services.media.resolution_state import stamp_video_media_resolution
from app.workers.apify_jobs_worker_helpers import (
    _platform_from_content_url,
    _url_host,
)


ProviderScrape = Callable[[str, str], dict[str, Any]]


def resolve_video_media(
    evidence: dict[str, Any],
    *,
    apify_configured: bool,
    scrape_with_timeout: ProviderScrape,
) -> dict[str, Any]:
    """Resolve a video input without conflating scrape and media readiness."""

    content_url = str(evidence.get("content_url") or "").strip()
    platform = _platform_from_content_url(content_url)
    output = stamp_video_media_resolution(
        {
            "ok": False,
            "platform": platform,
            "source_url_host": _url_host(content_url),
            "direct_video_url": "",
            "direct_video_url_host": "",
            "reason": "",
            "scraped_ok": False,
        },
        scrape_success=False,
        media_resolved=False,
        downloadable=False,
        confirmed_non_video=False,
    )
    if platform == "unsupported":
        output.update(reason="unsupported_platform", status="blocked")
        return output
    if platform == "youtube":
        output.update(ok=True, reason="youtube_direct_url_path", status="ready")
        return stamp_video_media_resolution(
            output, media_resolved=True, downloadable=True
        )
    if not apify_configured:
        output.update(reason="apify_not_configured", status="blocked")
        return output

    scraped = scrape_with_timeout(content_url, platform)
    if scraped.get("_timeout"):
        output.update(reason="media_resolve_timeout", status="failed")
        return output
    if scraped.get("_child_exit"):
        output.update(
            reason=f"media_resolve_child_exit: {scraped.get('error') or platform}",
            status="failed",
        )
        return output
    if scraped.get("_parse_error"):
        output.update(
            reason=str(scraped.get("error") or "media_resolve_parse_failed"),
            status="failed",
        )
        return output

    scrape_success = bool(scraped.get("scraped_ok"))
    output["scraped_ok"] = scrape_success
    output["media_kind"] = str(scraped.get("media_kind") or "").strip().lower()
    if scrape_success and (
        scraped.get("confirmed_non_video") is True
        or output["media_kind"] in {"image", "photo", "carousel"}
    ):
        output.update(
            reason=f"media_resolve_failed:{platform}:image_post_no_video_confirmed",
            status="failed",
            # Compatibility alias consumed by the existing terminal branch.
            no_video_confirmed=True,
        )
        return stamp_video_media_resolution(
            output,
            scrape_success=True,
            media_resolved=False,
            downloadable=False,
            confirmed_non_video=True,
        )

    direct_video_url = str(scraped.get("video_url") or "").strip()
    parsed_direct = urlparse(direct_video_url) if direct_video_url else None
    direct_http_url = bool(
        parsed_direct
        and parsed_direct.scheme in {"http", "https"}
        and parsed_direct.netloc
    )
    if not direct_http_url:
        detail = str(scraped.get("error") or "").strip()
        if not detail:
            detail = (
                "scraped_no_downloadable_url"
                if scrape_success
                else "scrape_empty_or_blocked"
            )
        elif direct_video_url:
            detail = "invalid_downloadable_video_url"
        output.update(
            reason=f"media_resolve_failed:{platform}:{detail}"[:240],
            status="failed",
        )
        return stamp_video_media_resolution(
            output,
            scrape_success=scrape_success,
            media_resolved=False,
            downloadable=False,
            confirmed_non_video=False,
        )

    output.update(
        ok=True,
        direct_video_url=direct_video_url,
        direct_video_url_host=_url_host(direct_video_url),
        reason="media_resolved",
        status="ready",
    )
    return stamp_video_media_resolution(
        output,
        scrape_success=scrape_success,
        media_resolved=True,
        downloadable=True,
        confirmed_non_video=False,
    )


__all__ = ["resolve_video_media"]
