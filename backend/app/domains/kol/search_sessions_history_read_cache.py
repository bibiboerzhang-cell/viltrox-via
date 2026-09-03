"""Per-employee read cache for the smart-search history list.

``list_history`` is a fan-out read: session rows, their items, live
apify_jobs states, Pool previews, worker health, two display gates and a
progress projection per session — about 80 ms and a 120 KB payload for the
default cockpit call.  Under a 50-request burst that became a 3.5 s p95.

The cache key carries a data-version probe scoped to the requesting employee:
row count and max ``updated_at`` of that employee's sessions and items (one
small aggregate, ~3 ms).  Every durable write on the session family touches
``updated_at`` or the row count (create/attach/record/mark/archive/restore),
so the next read after any such write rebuilds immediately without a single
write site having to call an invalidator — including worker processes without
Redis.  Projections that change without touching those tables (apify_jobs
progress, Pool preview refresh, worker health) are bounded by the short TTL.

The max timestamp is read as ``EXTRACT(EPOCH FROM MAX(updated_at))`` on
purpose: the DB compat layer renders timestamp columns at second precision,
and an archive followed by an undo-restore inside one wall-clock second
changes neither the row count nor that rendered value — only the sub-second
epoch separates the two states.  (On the sqlite fallback the probe fails and
the request is served uncached, never stale.)

Contract: the cached payload is the facade's dict, unchanged.  Only
``status == "ready"`` lists are cached, and a request whose employee identity
cannot be resolved is never cached (the facade already answers it honestly).
"""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from typing import Any

from app.core.logging import get_logger
from app.domains.kol.search_sessions_serde import (
    _int_or_none,
    _normalize_query_type,
    _normalize_status,
    _staff_user_id,
)
from app.services.cache import cache_get_or_build


logger = get_logger(__name__)

SEARCH_HISTORY_READ_CACHE_TTL_SECONDS = 30
CACHE_KEY_SCHEMA_VERSION = "v1"

DataVersionFn = Callable[[int], str | None]
ListHistoryFn = Callable[..., dict[str, Any]]


def _digest(parts: list[Any]) -> str:
    raw = json.dumps(parts, ensure_ascii=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def normalized_filters(
    *,
    limit: int = 20,
    status: str = "",
    query_type: str = "",
    item_limit: int = 5,
    archived: bool = False,
) -> dict[str, Any]:
    """Mirror ``list_history`` input normalization so equivalent calls share a key."""
    return {
        "limit": max(1, min(int(limit or 20), 50)),
        "status": _normalize_status(status) if status else "",
        "query_type": _normalize_query_type(query_type) if query_type else "",
        "item_limit": max(0, min(int(item_limit or 5), 10)),
        "archived": bool(archived),
    }


def _epoch_token(value: Any) -> str:
    """Render the probe's epoch (Decimal/float/str) verbatim; None means no rows."""
    return "" if value is None else str(value).strip()


def history_data_version(actor_id: int) -> str | None:
    """Return a version token for one employee's session family, or None if unreadable."""
    from app.db.connection import get_conn

    actor = int(actor_id)
    try:
        row = get_conn().execute(
            """
            SELECT
              (SELECT COUNT(*) FROM vkpi_kol_search_sessions WHERE created_by = ?) AS session_count,
              (SELECT EXTRACT(EPOCH FROM MAX(updated_at))
                 FROM vkpi_kol_search_sessions WHERE created_by = ?) AS session_latest,
              (SELECT COUNT(*) FROM vkpi_kol_search_session_items i
                 JOIN vkpi_kol_search_sessions s ON s.id = i.session_id
                 WHERE s.created_by = ?) AS item_count,
              (SELECT EXTRACT(EPOCH FROM MAX(i.updated_at)) FROM vkpi_kol_search_session_items i
                 JOIN vkpi_kol_search_sessions s ON s.id = i.session_id
                 WHERE s.created_by = ?) AS item_latest
            """,
            (actor, actor, actor, actor),
        ).fetchone()
    except Exception:
        # A failed probe degrades to the uncached facade — never to a 500 and
        # never to a stale entry that no version can invalidate.
        logger.warning("kol_search_history.read_cache_version_probe_failed", exc_info=True)
        return None
    data = dict(row) if row else {}
    return (
        f"s:{_int_or_none(data.get('session_count')) or 0}:{_epoch_token(data.get('session_latest'))}"
        f":i:{_int_or_none(data.get('item_count')) or 0}:{_epoch_token(data.get('item_latest'))}"
    )


def history_cache_key(*, actor_id: int, data_version: str, filters: dict[str, Any]) -> str:
    return (
        f"vkpi_kol_search_history:{CACHE_KEY_SCHEMA_VERSION}:actor:{int(actor_id)}:"
        f"data:{_digest([data_version])}:q:{_digest([filters])}"
    )


def cacheable_history_payload(value: Any) -> bool:
    """Cache complete history lists only."""
    if not isinstance(value, dict):
        return False
    return (
        str(value.get("status") or "").strip().lower() == "ready"
        and isinstance(value.get("items"), list)
    )


def _log_outcome(observation: dict[str, Any]) -> None:
    outcome = str(observation.get("outcome") or "unknown")[:40]
    builder_ms = observation.get("builder_ms")
    log = logger.info if builder_ms is not None else logger.debug
    log(
        "kol_search_history.read_cache outcome=%s elapsed_ms=%.3f builder_ms=%s",
        outcome,
        float(observation.get("elapsed_ms") or 0.0),
        f"{float(builder_ms):.3f}" if builder_ms is not None else "none",
    )


def cached_list_history(
    *,
    list_history_fn: ListHistoryFn,
    limit: int = 20,
    status: str = "",
    query_type: str = "",
    item_limit: int = 5,
    staff: dict[str, Any] | None = None,
    archived: bool = False,
    cache_get_or_build_fn: Callable[..., dict[str, Any]] = cache_get_or_build,
    data_version_fn: DataVersionFn | None = None,
    observe: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Serve the facade's ``list_history`` through the per-employee read cache."""
    # Resolved at call time so the module-level probe stays patchable.
    data_version_fn = data_version_fn or history_data_version

    def _build() -> dict[str, Any]:
        return list_history_fn(
            limit=limit,
            status=status,
            query_type=query_type,
            item_limit=item_limit,
            staff=staff,
            archived=archived,
        )

    actor_id = _staff_user_id(staff)
    started_at = time.perf_counter()
    data_version = data_version_fn(actor_id) if actor_id else None
    if not actor_id or data_version is None:
        value = _build()
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 3)
        (observe or _log_outcome)(
            {
                "outcome": "actor_unresolved_builder" if not actor_id else "version_unavailable_builder",
                "elapsed_ms": elapsed_ms,
                "builder_ms": elapsed_ms,
                "cache_candidate": False,
            }
        )
        return value
    filters = normalized_filters(
        limit=limit,
        status=status,
        query_type=query_type,
        item_limit=item_limit,
        archived=archived,
    )
    return cache_get_or_build_fn(
        history_cache_key(actor_id=actor_id, data_version=data_version, filters=filters),
        _build,
        ttl=SEARCH_HISTORY_READ_CACHE_TTL_SECONDS,
        cache_if=cacheable_history_payload,
        observe=observe or _log_outcome,
    )


__all__ = [
    "CACHE_KEY_SCHEMA_VERSION",
    "SEARCH_HISTORY_READ_CACHE_TTL_SECONDS",
    "cacheable_history_payload",
    "cached_list_history",
    "history_cache_key",
    "history_data_version",
    "normalized_filters",
]
