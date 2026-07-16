"""Bounded network transport and shared types for Dealer activity sync."""
from __future__ import annotations

import ipaddress
import json
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx

from app.domains.events import feed_adapters


class DealerActivitySyncUnavailable(RuntimeError):
    """The additive Dealer/Event control-plane schema is unavailable."""


class DealerActivitySourceBlocked(RuntimeError):
    """A source did not pass an activation, feed, or lease gate."""


@dataclass(frozen=True)
class FetchResult:
    payload: bytes
    http_status: int
    network_accessed: bool
    coverage_status: str = "unknown"
    pages_fetched: int = 1


def retry_delay(failure_count: Any) -> timedelta:
    try:
        previous = max(0, int(failure_count or 0))
    except (TypeError, ValueError):
        previous = 0
    return timedelta(minutes=min(24 * 60, 15 * (2 ** min(previous, 7))))


def success_delay(refresh_policy: Any) -> timedelta:
    return {
        "hourly": timedelta(hours=1),
        "daily": timedelta(days=1),
        "weekly": timedelta(days=7),
    }.get(str(refresh_policy or "daily").strip().casefold(), timedelta(days=1))


def fetch_public_feed(
    source: Mapping[str, Any], preflight: Mapping[str, Any], *, timeout: float = 20.0
) -> FetchResult:
    """Fetch one bounded snapshot; incomplete Tribe pagination fails later."""
    feed_url = str(preflight.get("feed_url") or "")
    parts = urlsplit(feed_url)
    parser_profile = str(source.get("parser_profile") or "")
    if parser_profile == feed_adapters.PARSER_TRIBE_JSON:
        feed_url = urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urlencode({"page": 1, "per_page": feed_adapters.MAX_FEED_ITEMS}),
                "",
            )
        )
        parts = urlsplit(feed_url)
    host = str(parts.hostname or "").strip().casefold()
    port = int(parts.port or 443)
    addresses = {
        item[4][0]
        for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    }
    if not addresses:
        raise DealerActivitySourceBlocked("feed_dns_resolution_empty")
    for value in addresses:
        if not ipaddress.ip_address(value).is_global:
            raise DealerActivitySourceBlocked("feed_dns_resolved_non_public_address")

    chunks: list[bytes] = []
    total = 0
    with httpx.Client(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
        headers={
            "Accept": "application/json,application/atom+xml,text/calendar,application/xml;q=0.9",
            "User-Agent": "V-KPI-Event-Radar/1.0 (+candidate-only)",
        },
    ) as client:
        with client.stream("GET", feed_url) as response:
            if response.status_code != 200:
                raise DealerActivitySourceBlocked(
                    f"feed_http_status_{response.status_code}"
                )
            raw_length = str(response.headers.get("content-length") or "").strip()
            if raw_length.isdecimal() and int(raw_length) > feed_adapters.MAX_PAYLOAD_BYTES:
                raise DealerActivitySourceBlocked("feed_payload_too_large")
            for chunk in response.iter_bytes(128 * 1024):
                total += len(chunk)
                if total > feed_adapters.MAX_PAYLOAD_BYTES:
                    raise DealerActivitySourceBlocked("feed_payload_too_large")
                chunks.append(chunk)
            payload = b"".join(chunks)
            coverage_status = "complete"
            if parser_profile == feed_adapters.PARSER_TRIBE_JSON:
                try:
                    page = json.loads(payload.decode("utf-8-sig"))
                    total_pages = int(page.get("total_pages"))
                    has_next = bool(page.get("next_rest_url"))
                except (
                    AttributeError,
                    TypeError,
                    ValueError,
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                ):
                    coverage_status = "unproven"
                else:
                    coverage_status = (
                        "complete" if total_pages <= 1 and not has_next else "incomplete"
                    )
            return FetchResult(
                payload=payload,
                http_status=response.status_code,
                network_accessed=True,
                coverage_status=coverage_status,
                pages_fetched=1,
            )


__all__ = [
    "DealerActivitySourceBlocked",
    "DealerActivitySyncUnavailable",
    "FetchResult",
    "fetch_public_feed",
    "retry_delay",
    "success_delay",
]
