"""Sync-owned adapter for the qualified refresh planning port."""
from __future__ import annotations

from typing import Any

from app.domains.sync import apify_batch_refresh


class ApifyQualifiedRefreshPlanner:
    """Expose the existing plan-only sync implementation through a narrow port."""

    def plan(
        self,
        *,
        limit: int,
        offset: int = 0,
        stale_before: str = "",
        stale_days: int = 0,
        platforms: set[str] | None = None,
        tiers: set[str] | None = None,
        max_posts: int = 1,
        max_concurrent: int | None = None,
        chunk_overrides: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        return apify_batch_refresh.qualified_apify_batch_plan(
            limit=limit,
            offset=offset,
            stale_before=stale_before,
            stale_days=stale_days,
            platforms=platforms,
            tiers=tiers,
            max_posts=max_posts,
            max_concurrent=max_concurrent,
            chunk_overrides=chunk_overrides,
        )


qualified_refresh_planner = ApifyQualifiedRefreshPlanner()
