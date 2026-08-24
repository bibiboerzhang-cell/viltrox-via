"""Read-only aggregation of evidence attached to folded Pool identities."""
from __future__ import annotations

from typing import Any, Callable

from app.domains.kol.pool_read_avatar_hydration import (
    bounded_profile_avatar_urls,
    profile_avatar_fallback_needed,
)
from app.domains.kol.pool_read_projection import project_pool_read_item


def _count_by_pool(conn: Any, table: str, pool_ids: list[int]) -> tuple[dict[int, int], bool]:
    if not pool_ids:
        return {}, True
    placeholders = ",".join("?" for _ in pool_ids)
    try:
        rows = conn.execute(
            f"SELECT kol_pool_id, COUNT(*) AS n FROM {table} "
            f"WHERE kol_pool_id IN ({placeholders}) GROUP BY kol_pool_id",
            tuple(pool_ids),
        ).fetchall()
    except Exception:
        return {}, False
    try:
        return {int(row["kol_pool_id"]): int(row["n"] or 0) for row in rows}, True
    except (KeyError, TypeError, ValueError):
        # Compatibility/test connections may return a generic row shape for
        # unknown optional tables.  Treat that as unavailable evidence rather
        # than allowing a bulk read to fail after the primary Pool query.
        return {}, False


def project_pool_list_items(
    conn: Any,
    rows: list[Any],
    selection: Any,
    *,
    mask_fn: Callable[..., dict[str, Any]],
    contact_visibility: str,
) -> list[dict[str, Any]]:
    raw_rows = [dict(row) for row in rows]
    hydration_ids = [
        int(row["id"])
        for row in raw_rows
        if row.get("id") and profile_avatar_fallback_needed(row.get("avatar_url"))
    ]
    hydrated_avatars = bounded_profile_avatar_urls(conn, hydration_ids)
    for row in raw_rows:
        pool_id = int(row.get("id") or 0)
        if pool_id in hydrated_avatars:
            row["raw_profile_avatar_url"] = hydrated_avatars[pool_id]
    items = [project_pool_read_item(row, selection) for row in raw_rows]
    scopes = {
        int(item["id"]): [int(item["id"]), *[int(value) for value in item.get("canonical_duplicate_ids") or []]]
        for item in items
    }
    pool_ids = sorted({pool_id for scope in scopes.values() for pool_id in scope})
    videos, videos_ok = _count_by_pool(conn, "vkpi_kol_video_evidence", pool_ids)
    deep, deep_ok = _count_by_pool(conn, "vkpi_kol_llm_deep_analysis_results", pool_ids)
    for item in items:
        scope = scopes[int(item["id"])]
        item["video_evidence_count"] = sum(videos.get(pool_id, 0) for pool_id in scope)
        item["llm_deep_analysis_count"] = sum(deep.get(pool_id, 0) for pool_id in scope)
        item["evidence_scope_pool_ids"] = scope
        item["evidence_scope_partial"] = not (videos_ok and deep_ok)
    return [mask_fn(item, contact_visibility=contact_visibility) for item in items]
