"""Initial job/lineage load for search-session reconciliation."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


def load_job_lineages(
    conn: Any,
    job_id: int,
    *,
    row_factory: Any,
    loads: Callable[[Any, Any], Any],
    search_session_lineages: Callable[[dict[str, Any]], list[dict[str, Any]]],
    int_or_none: Callable[[Any], int | None],
) -> tuple[dict[str, Any], dict[str, Any], dict[tuple[int, int], set[str]]] | None:
    with conn.cursor(row_factory=row_factory) as cur:
        cur.execute(
            "SELECT id, payload, last_error FROM apify_jobs WHERE id=%s",
            (int(job_id),),
        )
        row = cur.fetchone()
    if not row:
        return None
    payload = (
        row.get("payload")
        if isinstance(row.get("payload"), dict)
        else loads(row.get("payload"), {})
    )
    if not isinstance(payload, dict):
        return None
    lineages = search_session_lineages(payload)
    if not lineages:
        return None
    unique_lineages: dict[tuple[int, int], set[str]] = {}
    for entry in lineages:
        session_id = int_or_none(entry.get("search_session_id"))
        item_id = int_or_none(entry.get("search_session_item_id"))
        if not session_id or not item_id:
            continue
        unique_lineages.setdefault((int(session_id), int(item_id)), set()).add(
            str(entry.get("role") or "").strip().lower()
        )
    if not unique_lineages:
        return None
    return row, payload, unique_lineages


def resolve_item_state(
    conn: Any,
    *,
    existing_payload: dict[str, Any],
    roles: set[str],
    session_id: int,
    item_id: int,
    raw_status: str,
    reason: str,
    job_row: dict[str, Any],
    lineage_jobs_for_item: Callable[..., list[dict[str, Any]]],
    lineage_item_state: Callable[..., dict[str, Any]],
    search_session_job_state: Callable[[str, str], tuple[str, str]],
) -> tuple[str, str, dict[str, Any] | None, dict[str, Any] | None, bool]:
    downstream: dict[str, Any] | None = None
    optional_gaps: dict[str, Any] | None = None
    required_tasks_complete = False
    if any(roles):
        state = lineage_item_state(
            existing_payload,
            lineage_jobs_for_item(
                conn,
                session_id=int(session_id),
                item_id=int(item_id),
            ),
        )
        item_status = str(state.get("item_status") or "partial")
        stage = str(state.get("stage") or "analysis")
        downstream = state.get("downstream") if isinstance(state.get("downstream"), dict) else {}
        optional_gaps = (
            state.get("optional_gaps")
            if isinstance(state.get("optional_gaps"), dict)
            else {}
        )
        required_tasks_complete = bool(state.get("required_tasks_complete"))
    else:
        item_status, stage = search_session_job_state(
            raw_status, reason or job_row.get("last_error") or ""
        )
    return item_status, stage, downstream, optional_gaps, required_tasks_complete
