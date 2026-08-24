"""Canonical employee projection for bounded semantic-recall Pool hits."""
from __future__ import annotations

from typing import Any


def project_pool_recall_items(conn: Any, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from app.domains.kol.pool_read_projection import (
        prepare_pool_read_selection,
        project_pool_avatar,
    )

    requested_ids: list[int] = []
    for item in items:
        try:
            pool_id = int(item.get("id") or item.get("kol_pool_id") or 0)
        except (TypeError, ValueError):
            continue
        if pool_id:
            requested_ids.append(pool_id)
    if not requested_ids:
        return list(items)
    try:
        selection = prepare_pool_read_selection(
            conn, clause="WHERE duplicate_of_id IS NULL", params=(),
        )
    except Exception:
        return list(items)
    output: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in items:
        try:
            source_id = int(item.get("id") or item.get("kol_pool_id") or 0)
        except (TypeError, ValueError):
            continue
        if source_id not in selection.row_by_id:
            try:
                physical = conn.execute(
                    "SELECT duplicate_of_id FROM vkpi_kol_pool WHERE id=?", (source_id,),
                ).fetchone()
            except Exception:
                physical = None
            duplicate_id = int(dict(physical).get("duplicate_of_id") or 0) if physical else 0
            if not duplicate_id:
                output.append(dict(item))
                continue
            canonical_id = selection.canonical_by_id.get(duplicate_id, duplicate_id)
        else:
            canonical_id = selection.canonical_by_id.get(source_id, source_id)
        if source_id in selection.official_ids or canonical_id in selection.official_ids:
            continue
        if canonical_id in seen or canonical_id not in selection.visible_ids:
            continue
        seen.add(canonical_id)
        row = selection.row_by_id.get(canonical_id, {})
        projected = dict(item)
        if "id" in projected:
            projected["id"] = canonical_id
        if "kol_pool_id" in projected:
            projected["kol_pool_id"] = canonical_id
        display_name = str(row.get("display_name") or row.get("handle") or "").strip()
        if display_name and "title" in projected:
            projected["title"] = display_name[:120]
        projected.update(selection.audit_by_id.get(canonical_id) or {})
        projected.update(project_pool_avatar(row))
        output.append(projected)
    return output
