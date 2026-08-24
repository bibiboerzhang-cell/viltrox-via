"""Request-time avatar restoration for safe KOL Pool response templates."""
from __future__ import annotations

from typing import Any

from app.domains.kol.pool_read_avatar_hydration import (
    bounded_profile_avatar_urls,
    profile_avatar_fallback_needed,
)
from app.domains.kol.pool_read_projection import project_pool_avatar
from app.domains.kol.pool_read_response_cache import (
    cached_pool_avatar_ids,
    restore_cached_pool_avatars,
)


def restore_pool_response_cache_hit(conn: Any, selection: Any, payload: Any) -> Any:
    """Restore only redacted avatar IDs; never hydrate unrelated Pool rows."""
    pool_ids = cached_pool_avatar_ids(payload)
    if not pool_ids:
        return restore_cached_pool_avatars(payload, {})
    raw_ids = [
        pool_id
        for pool_id in pool_ids
        if profile_avatar_fallback_needed(
            (selection.row_by_id.get(pool_id) or {}).get("avatar_url")
        )
    ]
    raw_avatars = bounded_profile_avatar_urls(conn, raw_ids)
    avatars: dict[int, dict[str, Any]] = {}
    for pool_id in pool_ids:
        row = dict(selection.row_by_id.get(pool_id) or {})
        if pool_id in raw_avatars:
            row["raw_profile_avatar_url"] = raw_avatars[pool_id]
        if row:
            avatars[pool_id] = project_pool_avatar(row)
    return restore_cached_pool_avatars(payload, avatars)
