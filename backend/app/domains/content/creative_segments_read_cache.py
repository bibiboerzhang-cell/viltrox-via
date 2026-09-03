"""Read cache for the segment-level creative library search.

``segment_search`` scans every ready final_v1 analysis row (about 570 in the
local data set), decodes each JSON payload, probes thumbnail caches and builds
facets — roughly 250 ms of CPU per request.  Under a 50-request burst that CPU
contention is what turned the endpoint into a 4.5 s p95.

The library only changes when an analysis row lands or flips status, so a
cheap data-version probe (row count / max id / max ``updated_at`` of the ready
rows, one indexed aggregate) is folded into the cache key.  Any write to the
source table changes the version and the very next read rebuilds; unchanged
data is served from Redis/memory for a bounded TTL.  No write path has to
remember to invalidate, and cross-process invalidation works without Redis.
The max timestamp is read as ``EXTRACT(EPOCH FROM MAX(updated_at))`` because
the DB compat layer renders timestamps at second precision — two rewrites of
existing rows inside one second would otherwise share a version.

Contract: the cached payload is the builder's dict, unchanged.  Only
``status in {ready, empty}`` payloads are cached — error payloads never pin.
Ordering drift that does not touch the source table (view-count refreshes,
handle renames) is bounded by the TTL.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from typing import Any

from app.core.logging import get_logger
from app.domains.content.creative_segments import (
    FINAL_V1_DERIVE_METHOD,
    _MAX_LIMIT,
    _int_or_none,
    _normalize_focal,
    _text,
)
from app.services.cache import cache_get_or_build


logger = get_logger(__name__)

CREATIVE_SEGMENTS_READ_CACHE_TTL_SECONDS = 60
CACHE_KEY_SCHEMA_VERSION = "v1"
CACHEABLE_STATUSES = frozenset({"ready", "empty"})

DataVersionFn = Callable[[], str | None]
SegmentSearchFn = Callable[..., dict[str, Any]]


def _digest(parts: list[Any]) -> str:
    raw = json.dumps(parts, ensure_ascii=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def normalized_filters(
    query: str = "",
    style: str = "",
    focal: str = "",
    limit: int = 30,
) -> dict[str, Any]:
    """Mirror ``segment_search`` input normalization so equivalent calls share a key."""
    focal_input = _text(focal, 20)
    return {
        "query": _text(query, 120).lower(),
        "style": _text(style, 60).lower(),
        "focal": _normalize_focal(focal_input) or focal_input,
        "limit": max(1, min(_int_or_none(limit) or 30, _MAX_LIMIT)),
    }


def _epoch_token(value: Any) -> str:
    """Render the probe's epoch (Decimal/float/str) verbatim; None means no rows."""
    return "" if value is None else str(value).strip()


def final_v1_data_version() -> str | None:
    """Return a version token for the ready final_v1 rows, or None if unreadable."""
    from app.db.connection import get_conn

    try:
        row = get_conn().execute(
            """
            SELECT COUNT(*) AS n, MAX(id) AS max_id,
                   EXTRACT(EPOCH FROM MAX(updated_at)) AS latest
            FROM vkpi_analysis_cache
            WHERE target_type = 'video'
              AND derive_method = ?
              AND status = 'ready'
            """,
            (FINAL_V1_DERIVE_METHOD,),
        ).fetchone()
    except Exception:
        # A failed probe must degrade to the uncached builder, never to a 500
        # and never to a stale entry that no version can invalidate.
        logger.warning("creative_segments.read_cache_version_probe_failed", exc_info=True)
        return None
    data = dict(row) if row else {}
    return (
        f"n:{_int_or_none(data.get('n')) or 0}"
        f":id:{_int_or_none(data.get('max_id')) or 0}"
        f":at:{_epoch_token(data.get('latest'))}"
    )


def segment_search_cache_key(*, data_version: str, filters: dict[str, Any]) -> str:
    return (
        f"vkpi_creative_segments:search:{CACHE_KEY_SCHEMA_VERSION}:"
        f"data:{_digest([data_version])}:q:{_digest([filters])}"
    )


def cacheable_segment_payload(value: Any) -> bool:
    """Cache complete library answers only; error payloads are never pinned."""
    if not isinstance(value, dict):
        return False
    return str(value.get("status") or "").strip().lower() in CACHEABLE_STATUSES


def _log_outcome(observation: dict[str, Any]) -> None:
    outcome = str(observation.get("outcome") or "unknown")[:40]
    builder_ms = observation.get("builder_ms")
    log = logger.info if builder_ms is not None else logger.debug
    log(
        "creative_segments.read_cache outcome=%s elapsed_ms=%.3f builder_ms=%s",
        outcome,
        float(observation.get("elapsed_ms") or 0.0),
        f"{float(builder_ms):.3f}" if builder_ms is not None else "none",
    )


def cached_segment_search(
    query: str = "",
    style: str = "",
    focal: str = "",
    limit: int = 30,
    *,
    cache_get_or_build_fn: Callable[..., dict[str, Any]] = cache_get_or_build,
    data_version_fn: DataVersionFn | None = None,
    segment_search_fn: SegmentSearchFn | None = None,
    observe: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Serve ``segment_search`` through the version-keyed read cache."""
    if segment_search_fn is None:
        from app.domains.content.creative_segments import segment_search

        segment_search_fn = segment_search
    filters = normalized_filters(query=query, style=style, focal=focal, limit=limit)
    started_at = time.perf_counter()
    # Resolved at call time so the module-level probe stays patchable.
    data_version = (data_version_fn or final_v1_data_version)()
    if data_version is None:
        value = segment_search_fn(query=query, style=style, focal=focal, limit=limit)
        (observe or _log_outcome)(
            {
                "outcome": "version_unavailable_builder",
                "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 3),
                "builder_ms": round((time.perf_counter() - started_at) * 1000, 3),
                "cache_candidate": False,
            }
        )
        return value
    return cache_get_or_build_fn(
        segment_search_cache_key(data_version=data_version, filters=filters),
        lambda: segment_search_fn(query=query, style=style, focal=focal, limit=limit),
        ttl=CREATIVE_SEGMENTS_READ_CACHE_TTL_SECONDS,
        cache_if=cacheable_segment_payload,
        observe=observe or _log_outcome,
    )


__all__ = [
    "CACHEABLE_STATUSES",
    "CACHE_KEY_SCHEMA_VERSION",
    "CREATIVE_SEGMENTS_READ_CACHE_TTL_SECONDS",
    "cacheable_segment_payload",
    "cached_segment_search",
    "final_v1_data_version",
    "normalized_filters",
    "segment_search_cache_key",
]
