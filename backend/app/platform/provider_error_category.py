"""Shared, side-effect-free provider/job failure classification.

The ordering is part of the persisted job contract: a message may contain
multiple markers, so the first matching family must stay stable.
"""
from __future__ import annotations


_CATEGORY_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "provider_pressure",
        (
            "429",
            "resource_exhausted",
            "rate limit",
            "quota exceeded",
            "500",
            "502",
            "503",
            "504",
            "5xx",
            "internal server error",
            "server error",
            "unavailable",
            "service unavailable",
            "high demand",
            "temporarily overloaded",
        ),
    ),
    ("timeout", ("gemini_call_timeout",)),
    ("media_resolve", ("media_resolve_failed", "media_resolve")),
    ("download", ("yt-dlp", "yt_dlp", "direct_video_download_failed", "download_failed")),
    (
        "content_restricted",
        (
            "age restricted",
            "age-restricted",
            "private video",
            "login required",
            "sign in to confirm",
            "members-only",
            "subscriber-only",
            "this video is private",
            "account is private",
            "requires authentication",
        ),
    ),
    (
        "content_blocked",
        (
            "not available in your country",
            "geoblock",
            "geo",
            "blocked in",
            "copyright",
            "dmca",
            "has been removed",
            "account terminated",
            "content warning",
        ),
    ),
    (
        "content_unavailable",
        ("video unavailable", "not_found", "not found", "404", "does not exist", "deleted"),
    ),
    ("permanent", ("unsupported", "invalid_video_url", "not_video", "bad url")),
    ("stale_running", ("stale_running_reclaimed",)),
    (
        "code_error",
        (
            "modulenotfounderror",
            "importerror",
            "nameerror",
            "attributeerror",
            "typeerror",
            "keyerror",
            "valueerror",
            "indexerror",
            "syntaxerror",
            "unboundlocalerror",
            "traceback (most recent call last)",
            "no module named",
            "cannot import name",
        ),
    ),
)


def error_category(message: object) -> str:
    """Return the stable persisted category for a raw failure message."""

    text = str(message or "").lower()
    for category, markers in _CATEGORY_MARKERS:
        if any(marker in text for marker in markers):
            return category
    return "unknown"


# Compatibility name retained for existing worker/tests while ownership moves
# out of the worker layer.
_error_category = error_category

__all__ = ["error_category", "_error_category"]
