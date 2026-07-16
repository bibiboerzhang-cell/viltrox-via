"""History reads and soft-archive mutations for KOL search sessions."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.db.connection import get_conn
from app.domains.kol.contact_access import mask_contact_payload
from app.domains.kol.search_sessions_schema import TERMINAL_SESSION_STATUSES
from app.domains.kol.search_sessions_serde import (
    _dict,
    _int_or_none,
    _item_counts,
    _normalize_query_type,
    _normalize_status,
    _row_to_item,
    _row_to_session,
    _staff_user_id,
    _text,
)


GetConn = Callable[[], Any]
ReachDisplayGate = Callable[
    [Any, list[dict[str, Any]]],
    tuple[list[dict[str, Any]], dict[str, Any]],
]
PayloadMasker = Callable[[dict[str, Any]], dict[str, Any]]


def list_history(
    *,
    limit: int = 20,
    status: str = "",
    query_type: str = "",
    item_limit: int = 5,
    staff: dict[str, Any] | None = None,
    scope_to_staff: bool = True,
    archived: bool = False,
    get_conn_fn: GetConn | None = None,
    apply_reach_display_gate_fn: ReachDisplayGate | None = None,
    mask_contact_payload_fn: PayloadMasker | None = None,
) -> dict[str, Any]:
    """Return recent search sessions with compact item previews for history UI."""
    safe_limit = max(1, min(int(limit or 20), 50))
    safe_item_limit = max(0, min(int(item_limit or 5), 10))
    normalized_status = _normalize_status(status) if status else ""
    normalized_query_type = _normalize_query_type(query_type) if query_type else ""

    actor_id = _staff_user_id(staff) if scope_to_staff else None
    if scope_to_staff and not actor_id:
        return {
            "status": "ready",
            "count": 0,
            "items": [],
            "filters": {
                "status": normalized_status,
                "query_type": normalized_query_type,
                "limit": safe_limit,
                "item_limit": safe_item_limit,
                "archived": bool(archived),
                "scope": "current_staff_unresolved",
            },
        }

    where: list[str] = ["archived_at IS NOT NULL" if archived else "archived_at IS NULL"]
    params: list[Any] = []
    if normalized_status:
        where.append("status=?")
        params.append(normalized_status)
    if normalized_query_type:
        where.append("query_type=?")
        params.append(normalized_query_type)
    if actor_id:
        where.append("created_by=?")
        params.append(actor_id)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    conn = (get_conn_fn or get_conn)()
    rows = conn.execute(
        f"""
        SELECT *
        FROM vkpi_kol_search_sessions
        {where_sql}
        ORDER BY updated_at DESC, id DESC
        LIMIT ?
        """,
        (*params, safe_limit),
    ).fetchall()
    sessions = [_row_to_session(row) for row in rows]
    if not sessions:
        return {
            "status": "ready",
            "count": 0,
            "items": [],
            "filters": {
                "status": normalized_status,
                "query_type": normalized_query_type,
                "limit": safe_limit,
                "item_limit": safe_item_limit,
                "archived": bool(archived),
            },
        }

    session_ids = [int(session["id"]) for session in sessions if _int_or_none(session.get("id"))]
    placeholders = ", ".join(["?"] * len(session_ids))
    item_rows = conn.execute(
        f"""
        SELECT *
        FROM vkpi_kol_search_session_items
        WHERE session_id IN ({placeholders})
        ORDER BY session_id, rank NULLS LAST, id
        """,
        tuple(session_ids),
    ).fetchall()

    payload_masker = mask_contact_payload_fn or mask_contact_payload
    grouped: dict[int, list[dict[str, Any]]] = {int(session_id): [] for session_id in session_ids}
    for row in item_rows:
        item = _row_to_item(row)
        if isinstance(item.get("payload"), dict):
            item["payload"] = payload_masker(item["payload"])
        grouped.setdefault(int(item.get("session_id") or 0), []).append(item)

    if apply_reach_display_gate_fn is None:
        from app.domains.kol.search_sessions import _apply_reach_display_gate

        apply_reach_display_gate_fn = _apply_reach_display_gate

    history_items: list[dict[str, Any]] = []
    for session in sessions:
        session_id = int(session["id"])
        all_items = grouped.get(session_id, [])
        all_items, reach_counts = apply_reach_display_gate_fn(conn, all_items)
        counts = _item_counts(all_items)
        preview_items = all_items[:safe_item_limit] if safe_item_limit else []
        active_items = [
            item
            for item in all_items
            if _text(item.get("status")) in {"queued", "running", "already_queued"}
        ]
        result_summary = _dict(session.get("result_summary"))
        history_items.append(
            {
                **session,
                "item_count": len(all_items),
                "items_preview": preview_items,
                "active_items": active_items[:3],
                "counts": counts,
                "reach_floor_display": reach_counts,
                "summary": {
                    "kind": result_summary.get("kind"),
                    "platform": result_summary.get("platform"),
                    "url_type": result_summary.get("url_type"),
                    "in_pool": result_summary.get("in_pool"),
                    "items_written": result_summary.get("items_written"),
                    "matched_kol_pool_id": result_summary.get("matched_kol_pool_id"),
                    "viltrox_fit_score_untouched": result_summary.get("viltrox_fit_score_untouched"),
                },
            }
        )

    return {
        "status": "ready",
        "count": len(history_items),
        "items": history_items,
        "filters": {
            "status": normalized_status,
            "query_type": normalized_query_type,
            "limit": safe_limit,
            "item_limit": safe_item_limit,
            "archived": bool(archived),
        },
    }


def archive_history_session(
    session_id: int,
    *,
    staff: dict[str, Any] | None,
    reason: str = "user_removed",
    get_conn_fn: GetConn | None = None,
) -> dict[str, Any]:
    """Soft-archive one terminal search session owned by the current staff member."""
    actor_id = _staff_user_id(staff)
    if not actor_id:
        raise PermissionError("current staff identity is required")
    conn = (get_conn_fn or get_conn)()
    row = conn.execute(
        """
        SELECT * FROM vkpi_kol_search_sessions
        WHERE id=? AND created_by=?
        """,
        (int(session_id), actor_id),
    ).fetchone()
    if not row:
        raise LookupError(f"search session not found: {session_id}")
    current = dict(row)
    if current.get("archived_at"):
        session = _row_to_session(current)
        session["archive_status"] = "already_archived"
        return session
    status = _normalize_status(current.get("status"))
    if status not in TERMINAL_SESSION_STATUSES:
        raise ValueError("search session is still active; archive it after the task finishes")
    updated = conn.execute(
        """
        UPDATE vkpi_kol_search_sessions
        SET archived_at=NOW(), archived_by=?, archive_reason=?, updated_at=NOW()
        WHERE id=? AND created_by=? AND archived_at IS NULL
        RETURNING *
        """,
        (actor_id, _text(reason)[:160] or "user_removed", int(session_id), actor_id),
    ).fetchone()
    conn.commit()
    session = _row_to_session(updated or current)
    session["archive_status"] = "archived"
    return session


def restore_history_session(
    session_id: int,
    *,
    staff: dict[str, Any] | None,
    get_conn_fn: GetConn | None = None,
) -> dict[str, Any]:
    """Restore one archived search session to the current staff member's history."""
    actor_id = _staff_user_id(staff)
    if not actor_id:
        raise PermissionError("current staff identity is required")
    conn = (get_conn_fn or get_conn)()
    row = conn.execute(
        """
        SELECT * FROM vkpi_kol_search_sessions
        WHERE id=? AND created_by=?
        """,
        (int(session_id), actor_id),
    ).fetchone()
    if not row:
        raise LookupError(f"search session not found: {session_id}")
    current = dict(row)
    if not current.get("archived_at"):
        session = _row_to_session(current)
        session["archive_status"] = "already_active"
        return session
    updated = conn.execute(
        """
        UPDATE vkpi_kol_search_sessions
        SET archived_at=NULL, archived_by=NULL, archive_reason='', updated_at=NOW()
        WHERE id=? AND created_by=? AND archived_at IS NOT NULL
        RETURNING *
        """,
        (int(session_id), actor_id),
    ).fetchone()
    conn.commit()
    session = _row_to_session(updated or current)
    session["archive_status"] = "restored"
    return session


def archive_history_sessions(
    *,
    staff: dict[str, Any] | None,
    get_conn_fn: GetConn | None = None,
) -> dict[str, Any]:
    """Archive all terminal history rows owned by staff, preserving active work."""
    actor_id = _staff_user_id(staff)
    if not actor_id:
        raise PermissionError("current staff identity is required")
    conn = (get_conn_fn or get_conn)()
    archived_rows = conn.execute(
        """
        UPDATE vkpi_kol_search_sessions
        SET archived_at=NOW(), archived_by=?, archive_reason='user_cleared_completed', updated_at=NOW()
        WHERE created_by=? AND archived_at IS NULL
          AND status IN ('ready', 'partial', 'failed', 'cancelled')
        RETURNING id
        """,
        (actor_id, actor_id),
    ).fetchall()
    active_row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM vkpi_kol_search_sessions
        WHERE created_by=? AND archived_at IS NULL
          AND status NOT IN ('ready', 'partial', 'failed', 'cancelled')
        """,
        (actor_id,),
    ).fetchone()
    conn.commit()
    archived_ids = [int(dict(row)["id"]) for row in archived_rows if dict(row).get("id")]
    return {
        "status": "archived",
        "archived_count": len(archived_ids),
        "archived_session_ids": archived_ids,
        "skipped_active_count": int(dict(active_row or {}).get("n") or 0),
    }
