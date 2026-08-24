"""Employee-visible workspace facets for the canonical Pool projection."""
from __future__ import annotations

from typing import Any

from app.domains.kol.pool_read_projection import project_pool_avatar


def pool_read_data_status_ids(selection: Any, status: str) -> frozenset[int] | None:
    normalized = str(status or "").strip().lower()
    if normalized not in {"complete", "missing"}:
        return None
    complete: set[int] = set()
    for pool_id in selection.visible_ids:
        row = selection.row_by_id.get(pool_id) or {}
        avatar = project_pool_avatar(row, cached_avatar_lookup=lambda _url: "")
        avatar_ready = avatar.get("avatar_url_status") in {"durable", "ephemeral"} and bool(avatar.get("avatar_url"))
        metrics_ready = all(row.get(key) is not None for key in ("avg_views", "engagement_rate", "viltrox_fit_score"))
        if avatar_ready and metrics_ready:
            complete.add(int(pool_id))
    return frozenset(complete if normalized == "complete" else set(selection.visible_ids) - complete)


def pool_read_workspace_facets(
    conn: Any,
    selection: Any,
    table_columns: set[str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    ids = sorted(selection.visible_ids)
    if not ids:
        return [], {"complete": 0, "missing": 0}
    placeholders = ",".join("?" for _ in ids)
    predicate = f"id IN ({placeholders})"
    params = tuple(ids)
    by_candidate = []
    if "candidate_kind" in table_columns:
        rows = conn.execute(
            "SELECT COALESCE(NULLIF(candidate_kind, ''), 'unknown') AS candidate_kind, COUNT(*) AS n "
            f"FROM vkpi_kol_pool WHERE {predicate} "
            "GROUP BY COALESCE(NULLIF(candidate_kind, ''), 'unknown') ORDER BY n DESC, candidate_kind ASC",
            params,
        ).fetchall()
        by_candidate = [dict(row) for row in rows]
    complete_count = len(pool_read_data_status_ids(selection, "complete") or ())
    return by_candidate, {
        "complete": complete_count,
        "missing": len(ids) - complete_count,
    }
