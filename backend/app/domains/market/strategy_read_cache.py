"""Shared, tenant-partitioned cache for expensive strategy read aggregates.

The standalone strategy routes, GTM summary, and GTM preview all consume the
same category/benchmark aggregates.  Keeping the cache at the router layer
made the GTM surfaces repeat the same scans during one cockpit load.  These
wrappers provide one cache identity for every read consumer while preserving
the existing fail-closed legacy-tenant boundary.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.domains.market_brain.read_cache import cacheable_payload
from app.services.cache import cache_get_or_build


STRATEGY_READ_CACHE_TTL_SECONDS = 30


def category_tracks_cache_key(organization_id: int) -> str:
    return f"vkpi_strategy:category_tracks:v2:org:{int(organization_id)}"


def industry_benchmark_cache_key(organization_id: int, *, window_days: int) -> str:
    return (
        "vkpi_strategy:industry_benchmark:v2:"
        f"org:{int(organization_id)}:days:{int(window_days)}"
    )


def build_category_tracks_for_organization(organization_id: int) -> dict[str, Any]:
    from app.domains.market import category_tracks
    from app.domains.platform.tenancy import default_organization_id

    organization_id = int(organization_id)
    if organization_id != default_organization_id():
        return {
            "status": "scope_unavailable",
            "reason": "赛道聚合的底层评论/证据/目录尚未完成多租户字段收窄，未返回默认工作区数据。",
            "organization_id": organization_id,
        }
    return category_tracks.tracks()


def build_industry_benchmark_for_organization(
    organization_id: int,
    *,
    window_days: int,
) -> dict[str, Any]:
    from app.domains.market import industry_benchmark
    from app.domains.platform.tenancy import default_organization_id

    organization_id = int(organization_id)
    days = max(14, min(365, int(window_days or 90)))
    if organization_id != default_organization_id():
        return {
            "status": "scope_unavailable",
            "reason": "行业对照的底层证据/深析/目录尚未完成多租户字段收窄，未返回默认工作区数据。",
            "organization_id": organization_id,
            "window_days": days,
        }
    return industry_benchmark.benchmark(window_days=days)


def cached_category_tracks(
    organization_id: int,
    *,
    cache_get_or_build_fn: Callable[..., dict[str, Any]] = cache_get_or_build,
) -> dict[str, Any]:
    organization_id = int(organization_id)
    return cache_get_or_build_fn(
        category_tracks_cache_key(organization_id),
        lambda: build_category_tracks_for_organization(organization_id),
        ttl=STRATEGY_READ_CACHE_TTL_SECONDS,
        cache_if=cacheable_payload,
    )


def cached_industry_benchmark(
    organization_id: int,
    *,
    window_days: int = 90,
    cache_get_or_build_fn: Callable[..., dict[str, Any]] = cache_get_or_build,
) -> dict[str, Any]:
    organization_id = int(organization_id)
    days = max(14, min(365, int(window_days or 90)))
    return cache_get_or_build_fn(
        industry_benchmark_cache_key(organization_id, window_days=days),
        lambda: build_industry_benchmark_for_organization(
            organization_id,
            window_days=days,
        ),
        ttl=STRATEGY_READ_CACHE_TTL_SECONDS,
        cache_if=cacheable_payload,
    )


__all__ = [
    "STRATEGY_READ_CACHE_TTL_SECONDS",
    "build_category_tracks_for_organization",
    "build_industry_benchmark_for_organization",
    "cached_category_tracks",
    "cached_industry_benchmark",
    "category_tracks_cache_key",
    "industry_benchmark_cache_key",
]
