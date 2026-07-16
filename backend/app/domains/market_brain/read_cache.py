"""Truth-preserving cache policy for expensive GTM read aggregations.

The freshness generation is deliberately short lived.  It is not presented as
business-data provenance: it only prevents a rolling deploy or a later 30-second
read window from reusing an older aggregation.  Source rows may therefore be at
most one TTL old, which is the explicit UI freshness contract for these views.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any


UNCACHEABLE_STATUSES = frozenset(
    {
        "error",
        "degraded",
        "unavailable",
        "scope_unavailable",
    }
)


def freshness_version(method: str, *, ttl_seconds: int, now: float | None = None) -> str:
    """Return a code-method + bounded-time data freshness generation."""
    ttl = max(1, int(ttl_seconds))
    epoch = int((time.time() if now is None else float(now)) // ttl)
    method_digest = hashlib.sha256(str(method or "unknown").encode("utf-8")).hexdigest()[:12]
    return f"{method_digest}:{epoch}"


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


__all__ = ["UNCACHEABLE_STATUSES", "cacheable_payload", "freshness_version"]
