"""Management-only aggregate truth for current KOL search sessions.

The normal search-session readers are intentionally scoped to the current
employee and include query/card detail for that employee's UI.  This module is
the complementary operations view: it evaluates the same live progress
contract across employees, but returns counts and sealed release evidence
only.  Search text, staff identities, creator identities, handles, URLs and
raw payloads never enter the response.

The scan is explicitly bounded.  When the unarchived population exceeds the
requested limit, ``all_current_sessions_terminal`` is ``None`` rather than a
claim based on a partial sample.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.access import scope
from app.domains.kol.search_progress_contract import (
    observe_worker_health,
    project_search_progress,
)
from app.domains.kol.search_sessions_enrichment import (
    _enrichment_preview_status,
    _refresh_enrichment_queue_states,
)
from app.domains.kol.search_sessions_items import canonicalize_session_creator_items
from app.domains.kol.search_sessions_previews import hydrate_session_item_previews
from app.domains.kol.search_sessions_serde import (
    _int_or_none,
    _normalize_status,
    _row_to_item,
    _row_to_session,
)


TEAM_STATUS_SCHEMA = "kol_search_team_status_v1"
MAX_TEAM_STATUS_SESSIONS = 1000
logger = get_logger(__name__)
_EFFECTIVE_STATES = (
    "planned",
    "queued",
    "running",
    "active",
    "blocked_by_worker",
    "ready",
    "partial",
    "failed",
    "cancelled",
    "canceled",
    "unknown",
)
_STORED_STATUSES = ("planned", "running", "ready", "partial", "failed", "cancelled")

GetConn = Callable[[], Any]
ProgressProjector = Callable[..., dict[str, Any]]
WorkerObserver = Callable[[Any], dict[str, Any]]
ItemsMutator = Callable[[Any, list[dict[str, Any]]], None]
ItemsCanonicalizer = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
OrganizationGuard = Callable[[dict[str, Any] | None, Any], int]


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_limit(value: Any) -> int:
    try:
        parsed = int(value or MAX_TEAM_STATUS_SESSIONS)
    except (TypeError, ValueError):
        parsed = MAX_TEAM_STATUS_SESSIONS
    return max(1, min(parsed, MAX_TEAM_STATUS_SESSIONS))


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _release_evidence(worker: dict[str, Any]) -> dict[str, Any]:
    """Project only non-identifying app/worker release evidence."""

    raw_shas = worker.get("worker_shas")
    worker_shas = (
        sorted({str(value) for value in raw_shas if value})
        if isinstance(raw_shas, list)
        else []
    )
    return {
        "app_release_sha": worker.get("release_sha"),
        "app_release_sha_source": worker.get("release_sha_source"),
        "worker_release_sha": worker.get("worker_sha"),
        "worker_release_shas": worker_shas,
        "worker_sha_aligned": worker.get("sha_aligned"),
        "worker_state": worker.get("state") or "unknown",
        "worker_capacity_ready": worker.get("capacity_ready"),
        "worker_online_count": worker.get("online_count"),
        "worker_expected_count": worker.get("expected_count"),
        "worker_evidence_observed": worker.get("observed") is True,
        "worker_evidence_source": worker.get("source") or "vkpi_worker_heartbeat",
    }


def _default_refresh(conn: Any, items: list[dict[str, Any]]) -> None:
    _refresh_enrichment_queue_states(conn, items)


def _default_hydrate(conn: Any, items: list[dict[str, Any]]) -> None:
    hydrate_session_item_previews(
        conn,
        items,
        enrichment_status_fn=_enrichment_preview_status,
        logger=logger,
    )


def _default_organization_guard(staff: dict[str, Any] | None, conn: Any) -> int:
    # Search sessions predate organization_id.  They are legacy org-1 data and
    # must fail closed for every other workspace until an additive tenant
    # column/backfill exists.
    return scope.assert_legacy_default_organization(
        staff,
        conn,
        feature="KOL search team status",
    )


def build_team_search_status(
    *,
    staff: dict[str, Any] | None,
    limit: int = MAX_TEAM_STATUS_SESSIONS,
    get_conn_fn: GetConn | None = None,
    project_progress_fn: ProgressProjector = project_search_progress,
    observe_worker_fn: WorkerObserver = observe_worker_health,
    refresh_queue_states_fn: ItemsMutator = _default_refresh,
    hydrate_previews_fn: ItemsMutator = _default_hydrate,
    canonicalize_items_fn: ItemsCanonicalizer = canonicalize_session_creator_items,
    organization_guard_fn: OrganizationGuard = _default_organization_guard,
) -> dict[str, Any]:
    """Return a bounded, PII-free aggregate over unarchived search sessions.

    The effective terminal decision comes from ``project_search_progress`` and
    its ``requested_tasks_terminal`` flag, not from the stored session status.
    This keeps the manager view aligned with employee history/detail views when
    a durable queue row has advanced beyond a stale stored snapshot.
    """

    safe_limit = _safe_limit(limit)
    conn = (get_conn_fn or get_conn)()
    organization_id = int(organization_guard_fn(staff, conn))
    population_row = conn.execute(
        """
        SELECT COUNT(*) AS session_count,
               COUNT(DISTINCT created_by) AS staff_count
        FROM vkpi_kol_search_sessions
        WHERE archived_at IS NULL
        """
    ).fetchone()
    population = _safe_int(dict(population_row or {}).get("session_count"))
    staff_population = _safe_int(dict(population_row or {}).get("staff_count"))

    rows = conn.execute(
        """
        SELECT *
        FROM vkpi_kol_search_sessions
        WHERE archived_at IS NULL
        ORDER BY updated_at DESC, id DESC
        LIMIT ?
        """,
        (safe_limit,),
    ).fetchall()
    sessions = [_row_to_session(row) for row in rows]
    session_ids = [
        int(session_id)
        for session_id in (_int_or_none(session.get("id")) for session in sessions)
        if session_id
    ]

    grouped: dict[int, list[dict[str, Any]]] = {session_id: [] for session_id in session_ids}
    if session_ids:
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
        for row in item_rows:
            item = _row_to_item(row)
            session_id = _int_or_none(item.get("session_id"))
            if session_id in grouped:
                grouped[int(session_id)].append(item)

    all_items = [item for items in grouped.values() for item in items]
    if all_items:
        refresh_queue_states_fn(conn, all_items)
        hydrate_previews_fn(conn, all_items)

    worker = observe_worker_fn(conn)
    by_effective_state = {state: 0 for state in _EFFECTIVE_STATES}
    nonterminal_by_effective_state = {state: 0 for state in _EFFECTIVE_STATES}
    by_stored_status = {status: 0 for status in _STORED_STATUSES}
    terminal_count = 0
    nonterminal_count = 0
    blocked_count = 0
    orchestration_pending_count = 0
    full_analysis_complete_count = 0
    nonterminal_staff_ids: set[int] = set()

    for session in sessions:
        session_id = _int_or_none(session.get("id")) or 0
        items = canonicalize_items_fn(grouped.get(session_id, []))
        progress = project_progress_fn(session, items, worker_health=worker)
        effective_state = str(progress.get("state") or "unknown").strip().lower()
        if effective_state not in by_effective_state:
            effective_state = "unknown"
        by_effective_state[effective_state] += 1

        stored_status = _normalize_status(session.get("status"))
        by_stored_status[stored_status if stored_status in by_stored_status else "planned"] += 1

        is_terminal = progress.get("requested_tasks_terminal") is True
        if is_terminal:
            terminal_count += 1
        else:
            nonterminal_count += 1
            nonterminal_by_effective_state[effective_state] += 1
            created_by = _int_or_none(session.get("created_by"))
            if created_by:
                nonterminal_staff_ids.add(int(created_by))
        if progress.get("blocked_by_worker") is True:
            blocked_count += 1
        if progress.get("orchestration_pending") is True:
            orchestration_pending_count += 1
        if progress.get("full_analysis_complete") is True:
            full_analysis_complete_count += 1

    evaluated = len(sessions)
    coverage_complete = evaluated >= population
    observed_at = str(worker.get("observed_at") or _iso_now())
    nonzero_effective = {key: value for key, value in by_effective_state.items() if value}
    nonzero_stored = {key: value for key, value in by_stored_status.items() if value}
    nonzero_nonterminal = {
        key: value
        for key, value in nonterminal_by_effective_state.items()
        if value
    }

    return {
        "schema": TEAM_STATUS_SCHEMA,
        "status": "ready" if coverage_complete else "partial",
        "claim_status": "observed_read_only",
        "scope": {
            "mode": "all_staff_in_organization",
            "organization_id": organization_id,
            "management_only": True,
            "archived_sessions_included": False,
        },
        "coverage": {
            "session_population": population,
            "staff_population": staff_population,
            "evaluated_sessions": evaluated,
            "unevaluated_sessions": max(0, population - evaluated),
            "limit": safe_limit,
            "complete": coverage_complete,
            "truncated": not coverage_complete,
        },
        "counts": {
            "sessions_evaluated": evaluated,
            "requested_tasks_terminal": terminal_count,
            "requested_tasks_nonterminal": nonterminal_count,
            "staff_with_observed_nonterminal_sessions": len(nonterminal_staff_ids),
            "blocked_by_worker": blocked_count,
            "orchestration_pending": orchestration_pending_count,
            "full_analysis_complete": full_analysis_complete_count,
            "by_effective_state": nonzero_effective,
            "by_stored_status": nonzero_stored,
        },
        "nonterminal": {
            "observed_count": nonterminal_count,
            "by_effective_state": nonzero_nonterminal,
            "all_current_sessions_terminal": (
                nonterminal_count == 0 if coverage_complete else None
            ),
        },
        "release_evidence": _release_evidence(worker),
        "observed_at": observed_at,
        "sources": [
            "vkpi_kol_search_sessions",
            "vkpi_kol_search_session_items",
            "apify_jobs",
            "vkpi_worker_heartbeat",
        ],
    }


__all__ = [
    "MAX_TEAM_STATUS_SESSIONS",
    "TEAM_STATUS_SCHEMA",
    "build_team_search_status",
]
