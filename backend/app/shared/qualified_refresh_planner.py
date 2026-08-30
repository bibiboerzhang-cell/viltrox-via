"""Read-only contract for planning qualified creator refreshes.

The settings domain needs a projection of the next refresh batches, but it
must not depend on the sync implementation that selects rows and builds Apify
inputs.  Composition roots provide an implementation of this protocol.
"""
from __future__ import annotations

from typing import Any, Protocol


class QualifiedRefreshPlannerPort(Protocol):
    """Plan qualified refresh work without executing a provider or writing."""

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
    ) -> dict[str, Any]: ...
