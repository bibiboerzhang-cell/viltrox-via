"""Truthful shared-cache policy for employee-facing KOL Pool payloads."""
from __future__ import annotations

from typing import Any, Callable


def _contains_ephemeral_avatar(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("avatar_url_status") == "ephemeral" and value.get("avatar_url"):
            return True
        return any(_contains_ephemeral_avatar(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_ephemeral_avatar(item) for item in value)
    return False


def store_pool_read_payload(
    cache_set_fn: Callable[..., Any],
    key: str,
    payload: dict[str, Any],
    *,
    ttl: int,
) -> dict[str, Any]:
    """Never persist a signed avatar URL beyond its provider-owned lifetime."""
    if _contains_ephemeral_avatar(payload):
        return {
            **payload,
            "cache": {
                "hit": False,
                "ttl_sec": 0,
                "stored": False,
                "reason": "ephemeral_avatar",
            },
        }
    result = {**payload, "cache": {"hit": False, "ttl_sec": ttl, "stored": True}}
    cache_set_fn(key, result, ttl=ttl)
    return result
