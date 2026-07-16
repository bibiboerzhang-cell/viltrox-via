"""Access-log guard for signed upstream media URLs.

The browser-facing media proxy uses a GET query parameter because ``img`` and
``video`` elements cannot send a request body.  Uvicorn normally writes the
whole query string to ``uvicorn.access``; that would persist short-lived CDN
signatures even when Gunicorn's own access format uses path-only ``%(U)s``.
"""
from __future__ import annotations

import logging
from typing import Any


_MEDIA_PROXY_PATHS = frozenset(
    {
        "/api/admin/vkpi/media/image-proxy",
        "/api/admin/vkpi/media/video-proxy",
        "/api/admin/vkpi/media/video-redirect",
    }
)
_FILTER_MARKER = "_vkpi_media_proxy_query_guard"


def redact_media_proxy_request_target(target: object) -> str:
    """Drop the complete query string only for signed-media proxy routes."""

    text = str(target or "")
    path, separator, _query = text.partition("?")
    if separator and path in _MEDIA_PROXY_PATHS:
        return path
    return text


class MediaProxyAccessLogFilter(logging.Filter):
    """Redact Uvicorn's path argument before any handler formats the record."""

    def filter(self, record: logging.LogRecord) -> bool:
        args: Any = record.args
        if record.name == "uvicorn.access" and isinstance(args, tuple) and len(args) >= 3:
            sanitized = redact_media_proxy_request_target(args[2])
            if sanitized != args[2]:
                mutable = list(args)
                mutable[2] = sanitized
                record.args = tuple(mutable)
        return True


def install_media_proxy_access_log_filter() -> None:
    """Install the Uvicorn guard once per worker process."""

    access_logger = logging.getLogger("uvicorn.access")
    if getattr(access_logger, _FILTER_MARKER, False):
        return
    access_logger.addFilter(MediaProxyAccessLogFilter())
    setattr(access_logger, _FILTER_MARKER, True)


__all__ = [
    "MediaProxyAccessLogFilter",
    "install_media_proxy_access_log_filter",
    "redact_media_proxy_request_target",
]
