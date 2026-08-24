"""Truth-preserving cache policy and telemetry for expensive GTM reads.

Freshness is enforced by Redis/memory TTL, not by embedding a wall-clock bucket
in the key.  A stable method digest and explicit schema version invalidate
incompatible builders while allowing repeated requests throughout the full TTL
to hit the same entry.  Source rows may therefore be at most one TTL old, which
is the explicit UI freshness contract for these views.
"""
from __future__ import annotations

import hashlib
from typing import Any

from app.core.logging import get_logger


logger = get_logger(__name__)
CACHE_KEY_SCHEMA_VERSION = "v4"
_CACHE_TELEMETRY_SURFACES = frozenset({"summary", "plan_preview"})

UNCACHEABLE_STATUSES = frozenset(
    {
        "error",
        "degraded",
        "unavailable",
        "scope_unavailable",
    }
)


def cache_contract_version(method: str) -> str:
    """Return a stable digest for the builder contract encoded in ``method``."""
    method_digest = hashlib.sha256(str(method or "unknown").encode("utf-8")).hexdigest()[:12]
    return f"{CACHE_KEY_SCHEMA_VERSION}:{method_digest}"


def gtm_cache_observer(surface: str, *, response: Any = None):
    """Build a per-request observer backed by logs and safe response headers."""
    surface_candidate = str(surface or "").strip().lower()
    surface_clean = (
        surface_candidate
        if surface_candidate in _CACHE_TELEMETRY_SURFACES
        else "unknown"
    )

    def _observe(observation: dict[str, Any]) -> None:
        outcome = str(observation.get("outcome") or "unknown")[:40]
        elapsed_ms = float(observation.get("elapsed_ms") or 0.0)
        builder_ms_raw = observation.get("builder_ms")
        builder_ms = float(builder_ms_raw) if builder_ms_raw is not None else None
        logger.info(
            (
                "gtm.read_cache surface=%s outcome=%s elapsed_ms=%.3f "
                "builder_ms=%s key_schema=%s cache_candidate=%s"
            ),
            surface_clean,
            outcome,
            elapsed_ms,
            f"{builder_ms:.3f}" if builder_ms is not None else "none",
            CACHE_KEY_SCHEMA_VERSION,
            observation.get("cache_candidate"),
            extra={
                "surface": surface_clean,
                "cache_key_schema": CACHE_KEY_SCHEMA_VERSION,
                "cache_outcome": outcome,
                "cache_elapsed_ms": elapsed_ms,
                "cache_builder_ms": builder_ms,
                "cache_candidate": observation.get("cache_candidate"),
            },
        )
        if response is None:
            return
        response.headers["X-VKPI-Cache"] = outcome
        response.headers["X-VKPI-Cache-Builder"] = "1" if builder_ms is not None else "0"
        response.headers["X-VKPI-Cache-Key-Version"] = CACHE_KEY_SCHEMA_VERSION
        timings = [f'gtm-cache;desc="{outcome}";dur={elapsed_ms:.3f}']
        if builder_ms is not None:
            timings.append(f"gtm-builder;dur={builder_ms:.3f}")
        response.headers["Server-Timing"] = ", ".join(timings)

    return _observe


def cacheable_payload(value: Any) -> bool:
    """Only cache complete JSON-shaped payloads without degraded/error sections."""
    if not isinstance(value, dict):
        return False
    pending: list[Any] = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            status = str(item.get("status") or "").strip().lower()
            if status in UNCACHEABLE_STATUSES:
                return False
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
    return True


__all__ = [
    "CACHE_KEY_SCHEMA_VERSION",
    "UNCACHEABLE_STATUSES",
    "cache_contract_version",
    "cacheable_payload",
    "gtm_cache_observer",
]
