"""Authorization-scoped caches for dashboard summary assembly."""
from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from app.services.cache.memory_cache import cache_get, cache_set


SUMMARY_BLOCK_CACHE_TTL = 120


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
    return f"dash_summary:full:tenant={tenant}:scope={scope_key}:metric={metric_scope}:window={days}"


def cached_full_summary(
    *,
    cache_get_or_build_fn: Callable[..., dict[str, Any]],
    builder: Callable[[], dict[str, Any]],
    window_days: int,
    metric_scope: str,
    effective_staff_id: int | None,
    staff: dict[str, Any] | None,
    ttl: int,
) -> dict[str, Any]:
    """Collapse concurrent cold builds and return a defensive response copy."""

    actor = staff or {}
    tenant = summary_tenant_partition(staff)
    has_tenant = any(actor.get(key) not in (None, "") for key in ("organization_id", "workspace_id", "tenant_id"))
    has_authz = bool(actor.get("role") or actor.get("permissions_json") or actor.get("permissions"))
    if tenant is None or (not has_tenant and not has_authz):
        # A partial caller projection cannot prove a stable authorization
        # boundary. Build directly instead of sharing a possibly global value.
        return copy.deepcopy(builder())
    cache_key = full_summary_cache_key(
        window_days=window_days,
        metric_scope=metric_scope,
        effective_staff_id=effective_staff_id,
        staff=staff,
    )
    result = cache_get_or_build_fn(
        cache_key,
        builder,
        ttl=ttl,
        cache_if=lambda value: isinstance(value, dict) and not value.get("error"),
    )
    return copy.deepcopy(result)


__all__ = [
    "cached_full_summary",
    "cached_summary_block",
    "full_summary_cache_key",
    "summary_cache_key",
    "summary_tenant_partition",
]
