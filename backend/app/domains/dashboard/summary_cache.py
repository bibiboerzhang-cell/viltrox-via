"""Authorization-scoped caches for dashboard summary assembly."""
from __future__ import annotations

import copy
from collections.abc import Callable
import logging
import time
from typing import Any

from app.services.cache.memory_cache import cache_get, cache_set


SUMMARY_BLOCK_CACHE_TTL = 120
DASHBOARD_CACHE_KEY_SCHEMA_VERSION = "v1"
_CACHE_OUTCOMES = frozenset({
    "hit",
    "miss_builder",
    "miss_wait_hit",
    "miss_distributed_hit",
    "fenced_builder",
    "builder_error",
    "authz_bypass",
})
logger = logging.getLogger(__name__)


def _timing_ms(value: Any) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _emit_observation(
    observe: Callable[[dict[str, Any]], None] | None,
    payload: dict[str, Any],
) -> None:
    if observe is None:
        return
    try:
        observe(payload)
    except Exception:
        # Diagnostics must never replace a healthy dashboard response or mask
        # the builder's original exception.
        logger.warning("dashboard.cache_observer_failed", exc_info=True)


def dashboard_cache_observer(*, response: Any = None) -> Callable[[dict[str, Any]], None]:
    """Expose allowlisted cache diagnostics without leaking cache identity."""

    def observe(observation: dict[str, Any]) -> None:
        raw_outcome = str(observation.get("outcome") or "unknown")
        outcome = raw_outcome if raw_outcome in _CACHE_OUTCOMES else "unknown"
        elapsed_ms = _timing_ms(observation.get("elapsed_ms"))
        builder_ms = observation.get("builder_ms")
        logger.info(
            "dashboard.read_cache outcome=%s elapsed_ms=%.3f builder_ms=%s",
            outcome,
            elapsed_ms,
            f"{_timing_ms(builder_ms):.3f}" if builder_ms is not None else "none",
        )
        if response is None:
            return
        response.headers["X-VKPI-Cache"] = outcome
        response.headers["X-VKPI-Cache-Builder"] = "1" if builder_ms is not None else "0"
        response.headers["X-VKPI-Cache-Key-Version"] = DASHBOARD_CACHE_KEY_SCHEMA_VERSION
        metrics = [f'dashboard-cache;desc="{outcome}";dur={elapsed_ms:.3f}']
        if builder_ms is not None:
            metrics.append(f"dashboard-builder;dur={_timing_ms(builder_ms):.3f}")
        response.headers["Server-Timing"] = ", ".join(metrics)

    return observe


def summary_tenant_partition(staff: dict[str, Any] | None) -> str | None:
    """Return a stable authorization partition, or ``None`` when unprovable."""

    actor = staff or {}
    explicit = actor.get("organization_id") or actor.get("workspace_id") or actor.get("tenant_id")
    if explicit not in (None, ""):
        return str(explicit)
    actor_id = actor.get("id") or actor.get("staff_id")
    return f"actor-{actor_id}" if actor_id not in (None, "") else None


def summary_cache_key(name: str, staff_scope_id: int | None, **parts: Any) -> str:
    scope_key = str(staff_scope_id) if staff_scope_id else "global"
    key_parts = ":".join(f"{key}={parts[key]}" for key in sorted(parts))
    return f"dash_summary:{name}:scope={scope_key}:{key_parts}"


def cached_summary_block(
    name: str,
    staff_scope_id: int | None,
    builder: Callable[[], dict[str, Any]],
    *,
    tenant_partition: str | None = None,
    **key_parts: Any,
) -> dict[str, Any]:
    """Read through one authorization-scoped aggregate cache."""

    if not tenant_partition:
        return builder()
    key_parts["tenant"] = tenant_partition
    cache_key = summary_cache_key(name, staff_scope_id, **key_parts)
    hit = cache_get(cache_key)
    if hit is not None:
        return hit
    result = builder()
    cache_set(cache_key, result, SUMMARY_BLOCK_CACHE_TTL)
    return result


def full_summary_cache_key(
    *,
    window_days: int,
    metric_scope: str,
    effective_staff_id: int | None,
    staff: dict[str, Any] | None,
) -> str:
    """Partition the full response by server-resolved scope and tenant."""

    tenant = summary_tenant_partition(staff)
    if tenant is None:
        raise ValueError("dashboard_cache_tenant_unresolved")
    scope_key = str(int(effective_staff_id)) if effective_staff_id else "global"
    days = max(1, min(180, int(window_days or 30)))
    return (
        f"dash_summary:full:{DASHBOARD_CACHE_KEY_SCHEMA_VERSION}:tenant={tenant}:"
        f"scope={scope_key}:metric={metric_scope}:window={days}"
    )


def cached_full_summary(
    *,
    cache_get_or_build_fn: Callable[..., dict[str, Any]],
    builder: Callable[[], dict[str, Any]],
    window_days: int,
    metric_scope: str,
    effective_staff_id: int | None,
    staff: dict[str, Any] | None,
    ttl: int,
    observe: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Collapse concurrent cold builds and return a defensive response copy."""

    actor = staff or {}
    tenant = summary_tenant_partition(staff)
    has_tenant = any(actor.get(key) not in (None, "") for key in ("organization_id", "workspace_id", "tenant_id"))
    has_authz = bool(actor.get("role") or actor.get("permissions_json") or actor.get("permissions"))
    if tenant is None or (not has_tenant and not has_authz):
        # A partial caller projection cannot prove a stable authorization
        # boundary. Build directly instead of sharing a possibly global value.
        started = time.perf_counter()
        try:
            result = builder()
        except Exception:
            elapsed_ms = (time.perf_counter() - started) * 1000
            _emit_observation(observe, {
                "outcome": "builder_error",
                "elapsed_ms": elapsed_ms,
                "builder_ms": elapsed_ms,
                "cache_candidate": False,
            })
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000
        _emit_observation(observe, {
            "outcome": "authz_bypass",
            "elapsed_ms": elapsed_ms,
            "builder_ms": elapsed_ms,
            "cache_candidate": None,
        })
        return copy.deepcopy(result)
    cache_key = full_summary_cache_key(
        window_days=window_days,
        metric_scope=metric_scope,
        effective_staff_id=effective_staff_id,
        staff=staff,
    )
    cache_kwargs: dict[str, Any] = {
        "ttl": ttl,
        "cache_if": lambda value: isinstance(value, dict) and not value.get("error"),
    }
    if observe is not None:
        cache_kwargs["observe"] = observe
    result = cache_get_or_build_fn(cache_key, builder, **cache_kwargs)
    return copy.deepcopy(result)


__all__ = [
    "cached_full_summary",
    "cached_summary_block",
    "dashboard_cache_observer",
    "DASHBOARD_CACHE_KEY_SCHEMA_VERSION",
    "full_summary_cache_key",
    "summary_cache_key",
    "summary_tenant_partition",
]
