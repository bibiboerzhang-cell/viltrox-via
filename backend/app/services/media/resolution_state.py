"""Truthful state contract for IG/TikTok video-media resolution.

The provider can successfully scrape a post without returning bytes that the
video analyzer can consume.  Those are different facts and must stay different
all the way through Apify, yt-dlp and the durable video worker:

``scrape_success``
    Public post metadata was fetched successfully.  This does *not* prove that
    the post contains a video or that a downloadable video URL was returned.
``media_resolved``
    A concrete video input (direct URL or validated local/cache file) exists.
``downloadable``
    The concrete input is eligible for the worker download/local-file path.
    Network availability is still checked by the downloader.
``confirmed_non_video``
    Explicit provider metadata or the secondary extractor proved that the post
    contains images only.  Ambiguous/no-format failures must never set it.

The helper is deliberately pure so both the worker and the yt-dlp fallback can
stamp the same additive contract without a circular import.
"""
from __future__ import annotations

from typing import Any, Mapping


CONTRACT = "video_media_resolution_v1"


def _flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def stamp_video_media_resolution(
    value: Mapping[str, Any] | None,
    *,
    scrape_success: bool | None = None,
    media_resolved: bool | None = None,
    downloadable: bool | None = None,
    confirmed_non_video: bool | None = None,
) -> dict[str, Any]:
    """Return ``value`` with the four-state contract and invariants applied.

    Legacy aliases (``scraped_ok`` / ``no_video_confirmed``) are accepted on
    input so an old persisted result can pass through a new worker safely.
    ``confirmed_non_video`` always wins over video readiness; a confirmed image
    can therefore never be presented as a downloadable video.
    """

    result = dict(value or {})
    scraped = (
        _flag(scrape_success)
        if scrape_success is not None
        else _flag(result.get("scrape_success", result.get("scraped_ok")))
    )
    non_video = (
        _flag(confirmed_non_video)
        if confirmed_non_video is not None
        else _flag(result.get("confirmed_non_video", result.get("no_video_confirmed")))
    )
    can_download = (
        _flag(downloadable)
        if downloadable is not None
        else (
            _flag(result.get("downloadable"))
            or bool(
                _flag(result.get("ok"))
                and (
                    str(result.get("direct_video_url") or "").strip()
                    or str(result.get("path") or "").strip()
                )
            )
        )
    )
    resolved = (
        _flag(media_resolved)
        if media_resolved is not None
        else _flag(result.get("media_resolved"))
    )

    if non_video:
        resolved = False
        can_download = False
        state = "confirmed_non_video"
    elif can_download:
        resolved = True
        state = "local_video_ready" if result.get("path") else "direct_video_ready"
    elif resolved:
        # A concrete media object may be known while policy/format/size still
        # makes it ineligible for the downloader.  Preserve that distinction;
        # otherwise the resolver would needlessly probe the public post again.
        state = "media_resolved_not_downloadable"
    elif scraped:
        resolved = False
        state = "metadata_only"
    else:
        resolved = False
        state = "unresolved"

    result.update(
        {
            "media_resolution_contract": CONTRACT,
            "media_resolution_state": state,
            "scrape_success": scraped,
            "media_resolved": resolved,
            "downloadable": can_download,
            "confirmed_non_video": non_video,
        }
    )
    return result


def needs_secondary_video_probe(value: Mapping[str, Any] | None) -> bool:
    """Whether metadata-only scrape truth needs the conservative yt-dlp probe."""

    state = stamp_video_media_resolution(value)
    return bool(
        state["scrape_success"]
        and not state["media_resolved"]
        and not state["downloadable"]
        and not state["confirmed_non_video"]
    )


__all__ = ["CONTRACT", "needs_secondary_video_probe", "stamp_video_media_resolution"]
