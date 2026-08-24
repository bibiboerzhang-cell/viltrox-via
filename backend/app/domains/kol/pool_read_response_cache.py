"""Truthful shared-cache policy for employee-facing KOL Pool payloads."""
from __future__ import annotations

from typing import Any, Callable


_REDACTED_AVATAR_MARKER = "_pool_cache_ephemeral_avatar_redacted"
_AVATAR_FIELDS = (
    "avatar_url",
    "avatar_url_status",
    "avatar_upstream_status",
    "avatar_url_source",
    "avatar_fallback",
    "avatar_health",
)
_PRIVATE_AVATAR_EVIDENCE_KEYS = frozenset({
    "raw_profile_avatar_url",
    "raw_profile_doc",
})


def _cache_safe_copy(value: Any) -> tuple[Any, int]:
    if isinstance(value, list):
        output: list[Any] = []
        redacted = 0
        for item in value:
            safe_item, item_count = _cache_safe_copy(item)
            output.append(safe_item)
            redacted += item_count
        return output, redacted
    if not isinstance(value, dict):
        return value, 0
    output: dict[str, Any] = {}
    redacted = 0
    for key, item in value.items():
        if key in _PRIVATE_AVATAR_EVIDENCE_KEYS:
            continue
        safe_item, item_count = _cache_safe_copy(item)
        output[key] = safe_item
        redacted += item_count
    if value.get("avatar_url_status") == "ephemeral" and value.get("avatar_url"):
        output.update({
            "avatar_url": "",
            "avatar_url_status": "missing",
            "avatar_upstream_status": "ephemeral",
            "avatar_url_source": "initials_fallback",
            "avatar_fallback": "initials",
            "avatar_health": {
                "status": "missing",
                "upstream_status": "ephemeral",
                "source": "initials_fallback",
                "fallback": "initials",
            },
            _REDACTED_AVATAR_MARKER: True,
        })
        redacted += 1
    return output, redacted


def cached_pool_avatar_ids(value: Any) -> frozenset[int]:
    """Return IDs whose signed avatar was deliberately omitted from cache."""
    found: set[int] = set()

    def visit(item: Any) -> None:
        if isinstance(item, list):
            for child in item:
                visit(child)
            return
        if not isinstance(item, dict):
            return
        if item.get(_REDACTED_AVATAR_MARKER):
            try:
                pool_id = int(item.get("id") or item.get("canonical_pool_id") or 0)
            except (TypeError, ValueError):
                pool_id = 0
            if pool_id > 0:
                found.add(pool_id)
        for child in item.values():
            visit(child)

    visit(value)
    return frozenset(found)


def restore_cached_pool_avatars(
    value: Any,
    avatars_by_id: dict[int, dict[str, Any]],
) -> Any:
    """Remove internal markers and merge request-time avatar projections."""
    if isinstance(value, list):
        return [restore_cached_pool_avatars(item, avatars_by_id) for item in value]
    if not isinstance(value, dict):
        return value
    restored = {
        key: restore_cached_pool_avatars(item, avatars_by_id)
        for key, item in value.items()
        if key != _REDACTED_AVATAR_MARKER
    }
    if value.get(_REDACTED_AVATAR_MARKER):
        try:
            pool_id = int(value.get("id") or value.get("canonical_pool_id") or 0)
        except (TypeError, ValueError):
            pool_id = 0
        avatar = avatars_by_id.get(pool_id) or {}
        restored.update({key: avatar[key] for key in _AVATAR_FIELDS if key in avatar})
    return restored


def store_pool_read_payload(
    cache_set_fn: Callable[..., Any],
    key: str,
    payload: dict[str, Any],
    *,
    ttl: int,
) -> dict[str, Any]:
    """Cache a safe template while keeping signed avatars request-scoped.

    Ephemeral provider URLs are replaced by an honest initials fallback in the
    cached copy.  Callers re-project only those marked creator IDs on a cache
    hit, so the URL itself never survives beyond the request that validated it.
    """
    safe_payload, redacted = _cache_safe_copy(payload)
    cache_meta = {
        "hit": False,
        "ttl_sec": ttl,
        "stored": True,
        "ephemeral_avatar_urls_stored": 0,
        "ephemeral_avatar_templates": redacted,
    }
    cached_result = {**safe_payload, "cache": cache_meta}
    cache_set_fn(key, cached_result, ttl=ttl)
    return {**payload, "cache": cache_meta}
