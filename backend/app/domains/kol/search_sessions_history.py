"""History reads and soft-archive mutations for KOL search sessions."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.kol.contact_access import mask_contact_payload
from app.domains.kol.search_sessions_schema import TERMINAL_SESSION_STATUSES
from app.domains.kol.search_progress_contract import (
    observe_worker_health,
    project_search_progress,
    unobserved_worker_health,
)
from app.domains.kol.search_sessions_items import (
    _CREATOR_ITEM_LANES,
    _session_creator_probe,
    canonicalize_session_creator_items,
)
from app.domains.kol.search_sessions_previews import (
    hydrate_session_item_avatar_fallbacks,
)
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


logger = get_logger(__name__)


GetConn = Callable[[], Any]
ReachDisplayGate = Callable[
    [Any, list[dict[str, Any]]],
    tuple[list[dict[str, Any]], dict[str, Any]],
]
PayloadMasker = Callable[[dict[str, Any]], dict[str, Any]]


def apply_discovery_account_display_gate(
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Hide confirmed official accounts from discovery history projections.

    Historical evidence rows remain untouched.  The shared conservative gate
    requires identity/profile evidence, so a display name that merely mentions
    Viltrox (for example an independent reviewer) does not disappear.
    """
    from app.domains.kol.discovery_filters import discovery_account_gate_verdict

    kept: list[dict[str, Any]] = []
    counts = {
        "excluded_total": 0,
        "excluded_own_brand": 0,
        "excluded_brand_official": 0,
        "history_rows_deleted": 0,
        "basis": "conservative_discovery_account_gate_v1",
    }
    for item in items:
        if _text(item.get("item_type")) not in _CREATOR_ITEM_LANES:
            kept.append(item)
            continue
        verdict = discovery_account_gate_verdict(_session_creator_probe(item))
        if verdict not in {"own_brand", "brand_official"}:
            kept.append(item)
            continue
        counts["excluded_total"] += 1
        counts[f"excluded_{verdict}"] += 1
    return kept, counts


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
            "worker_health": unobserved_worker_health(reason="no_sessions_worker_probe_skipped"),
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

    hydrate_session_item_avatar_fallbacks(
        conn,
        [item for items in grouped.values() for item in items],
        logger=logger,
    )

    if apply_reach_display_gate_fn is None:
        from app.domains.kol.search_sessions import _apply_reach_display_gate

        apply_reach_display_gate_fn = _apply_reach_display_gate

    worker_health = observe_worker_health(conn)
    history_items: list[dict[str, Any]] = []
    account_gate_totals = {
        "excluded_total": 0,
        "excluded_own_brand": 0,
        "excluded_brand_official": 0,
        "history_rows_deleted": 0,
        "basis": "conservative_discovery_account_gate_v1",
    }
    for session in sessions:
        session_id = int(session["id"])
        all_items = canonicalize_session_creator_items(grouped.get(session_id, []))
        # Execution progress remains based on every durable task/evidence row.
        # The official-account gate is a display projection only and must not
        # turn a terminal session into an apparently incomplete one.
        progress_contract = project_search_progress(
            session,
            all_items,
            worker_health=worker_health,
        )
        all_items, account_gate_counts = apply_discovery_account_display_gate(all_items)
        for key in ("excluded_total", "excluded_own_brand", "excluded_brand_official"):
            account_gate_totals[key] += int(account_gate_counts[key])
        session["progress_contract"] = progress_contract
        session["effective_status"] = progress_contract.get("state")
        session["worker_health"] = dict(worker_health)
        result_summary = _dict(session.get("result_summary")).copy()
        result_summary["progress_contract"] = progress_contract
        session["result_summary"] = result_summary
        all_items, reach_counts = apply_reach_display_gate_fn(conn, all_items)
        counts = _item_counts(all_items)
        preview_items = all_items[:safe_item_limit] if safe_item_limit else []
        active_items = [
            item
            for item in all_items
            if _text(item.get("status")) in {"queued", "running", "already_queued"}
        ]
        history_items.append(
            {
                **session,
                "item_count": len(all_items),
                "items_preview": preview_items,
                "active_items": active_items[:3],
                "counts": counts,
                "reach_floor_display": reach_counts,
                "discovery_account_display_gate": account_gate_counts,
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
        "worker_health": worker_health,
        "discovery_account_display_gate": account_gate_totals,
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
