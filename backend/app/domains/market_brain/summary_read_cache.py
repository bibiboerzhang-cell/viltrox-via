"""Warm-before-expiry policy for the GTM summary read cache.

The summary card is already served from ``cache_get_or_build`` (a hit costs a
few milliseconds), but every TTL expiry makes one request pay the full ~1.9 s
aggregate — and under a 50-request burst that cold rebuild competes for the
interpreter with everything else, which is where the 4.7 s p95 came from.

This wrapper keeps the existing entry, key, TTL and cache-if policy untouched
and adds one thing: once a served entry is older than half its TTL, a single
background refresh per process rebuilds it *before* it expires.  Steady traffic
therefore never observes a cold miss, while the freshness contract documented
in ``read_cache`` is preserved — a served payload is never older than one TTL
because no stale value is ever served past expiry.  When traffic is idle for
longer than a TTL the next request still pays the honest cold build.

The refresh runs the builder under its own bounded DB scope (the same
invariant ``parallel_reads`` keeps), reuses the token-safe manual-refresh path
of ``cache_get_or_build`` so concurrent refreshers collapse, never raises into
a request, and stays inert while cache mutations are fenced for release
validation.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from app.core.logging import get_logger
from app.core.release_validation import release_validation_active
from app.db.connection import db_connection_sync_scope
from app.services.cache import cache_get, cache_get_or_build, cache_set


logger = get_logger(__name__)

SUMMARY_EARLY_REFRESH_RATIO = 0.5
_FRESH_BUILD_OUTCOMES = frozenset({"miss_builder", "refresh_builder"})

_REFRESH_GUARD = threading.Lock()
_REFRESH_INFLIGHT: set[str] = set()

Observer = Callable[[dict[str, Any]], None]
RefreshScheduler = Callable[[Callable[[], None]], None]


def built_at_key(key: str) -> str:
    return f"{key}:built_at"


def _default_refresh_scheduler(run: Callable[[], None]) -> None:
    threading.Thread(target=run, name="gtm-summary-early-refresh", daemon=True).start()


def _fresh_build_observed(observations: list[dict[str, Any]]) -> bool:
    return any(
        str(item.get("outcome") or "") in _FRESH_BUILD_OUTCOMES
        and item.get("cache_candidate") is True
        for item in observations
    )


def _stamp_built_at(
    key: str,
    observations: list[dict[str, Any]],
    *,
    ttl: int,
    cache_set_fn: Callable[..., None],
    now_fn: Callable[[], float],
) -> bool:
    """Record the build time of a freshly cached value under the same TTL."""
    if not _fresh_build_observed(observations):
        return False
    cache_set_fn(built_at_key(key), {"built_at": float(now_fn())}, ttl=int(ttl))
    return True


def entry_age_seconds(
    key: str,
    *,
    cache_get_fn: Callable[[str], Any],
    now_fn: Callable[[], float],
) -> float | None:
    """Return how old the cached entry is, or None when its build time is unknown."""
    meta = cache_get_fn(built_at_key(key))
    if not isinstance(meta, dict):
        return None
    try:
        built_at = float(meta.get("built_at"))
    except (TypeError, ValueError):
        return None
    return max(0.0, float(now_fn()) - built_at)


def refresh_inflight(key: str) -> bool:
    with _REFRESH_GUARD:
        return key in _REFRESH_INFLIGHT


def _claim_refresh(key: str) -> bool:
    with _REFRESH_GUARD:
        if key in _REFRESH_INFLIGHT:
            return False
        _REFRESH_INFLIGHT.add(key)
        return True


def _release_refresh(key: str) -> None:
    with _REFRESH_GUARD:
        _REFRESH_INFLIGHT.discard(key)


def schedule_early_refresh(
    key: str,
    builder: Callable[[], Any],
    *,
    ttl: int,
    cache_if: Callable[[Any], bool] | None,
    cache_get_fn: Callable[[str], Any] = cache_get,
    cache_set_fn: Callable[..., None] = cache_set,
    cache_get_or_build_fn: Callable[..., Any] = cache_get_or_build,
    refresh_scheduler: RefreshScheduler = _default_refresh_scheduler,
    now_fn: Callable[[], float] = time.time,
) -> bool:
    """Start one background rebuild when the served entry is past half its TTL."""
    age = entry_age_seconds(key, cache_get_fn=cache_get_fn, now_fn=now_fn)
    if age is None or age < float(ttl) * SUMMARY_EARLY_REFRESH_RATIO:
        return False
    if release_validation_active() or not _claim_refresh(key):
        return False

    def _run() -> None:
        observations: list[dict[str, Any]] = []
        try:
            with db_connection_sync_scope(release_validation_guard=True):
                cache_get_or_build_fn(
                    key,
                    builder,
                    ttl=int(ttl),
                    cache_if=cache_if,
                    observe=observations.append,
                    force_refresh=True,
                )
            _stamp_built_at(key, observations, ttl=ttl, cache_set_fn=cache_set_fn, now_fn=now_fn)
            logger.info(
                "gtm.summary_early_refresh outcome=%s",
                observations[-1].get("outcome") if observations else "unknown",
            )
        except Exception:
            # A background refresh must never surface as a request error; the
            # existing entry keeps serving until its own TTL expires.
            logger.warning("gtm.summary_early_refresh_failed", exc_info=True)
        finally:
            _release_refresh(key)

    refresh_scheduler(_run)
    return True


def cached_summary(
    key: str,
    builder: Callable[[], Any],
    *,
    ttl: int,
    cache_if: Callable[[Any], bool] | None,
    observe: Observer | None = None,
    force_refresh: bool = False,
    cache_get_fn: Callable[[str], Any] = cache_get,
    cache_set_fn: Callable[..., None] = cache_set,
    cache_get_or_build_fn: Callable[..., Any] = cache_get_or_build,
    refresh_scheduler: RefreshScheduler = _default_refresh_scheduler,
    now_fn: Callable[[], float] = time.time,
) -> Any:
    """Serve the summary from cache, warming it in the background before expiry."""
    started_at = time.perf_counter()
    if not force_refresh:
        value = cache_get_fn(key)
        if value is not None:
            if observe is not None:
                observe(
                    {
                        "outcome": "hit",
                        "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 3),
                        "builder_ms": None,
                        "cache_candidate": None,
                    }
                )
            schedule_early_refresh(
                key,
                builder,
                ttl=ttl,
                cache_if=cache_if,
                cache_get_fn=cache_get_fn,
                cache_set_fn=cache_set_fn,
                cache_get_or_build_fn=cache_get_or_build_fn,
                refresh_scheduler=refresh_scheduler,
                now_fn=now_fn,
            )
            return value

    observations: list[dict[str, Any]] = []

    def _record(observation: dict[str, Any]) -> None:
        observations.append(observation)
        if observe is not None:
            observe(observation)

    value = cache_get_or_build_fn(
        key,
        builder,
        ttl=int(ttl),
        cache_if=cache_if,
        observe=_record,
        force_refresh=force_refresh,
    )
    _stamp_built_at(key, observations, ttl=ttl, cache_set_fn=cache_set_fn, now_fn=now_fn)
    return value


__all__ = [
    "SUMMARY_EARLY_REFRESH_RATIO",
    "built_at_key",
    "cached_summary",
    "entry_age_seconds",
    "refresh_inflight",
    "schedule_early_refresh",
]
