"""Management-only aggregate truth for current KOL search sessions.

The normal search-session readers are intentionally scoped to the current
employee and include query/card detail for that employee's UI.  This module is
the complementary operations view: it evaluates the same live progress
contract across employees, but returns counts and sealed release evidence
only.  Search text, staff identities, creator identities, handles, URLs and
raw payloads never enter the response.

The scan is explicitly bounded and keyset-paginated.  ``limit`` remains the
compatible request parameter, but is now a batch-size hint rather than a
global sample cap.  When the population exceeds the independent scan budget,
``all_current_sessions_terminal`` is ``None`` rather than a partial claim.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.core.coerce import _loads
from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime
from app.domains.access import scope
from app.domains.kol.search_progress_contract import (
    observe_worker_health,
    project_search_progress,
)
from app.domains.kol.search_sessions_enrichment import _refresh_enrichment_queue_states
from app.domains.kol.search_sessions_items import canonicalize_session_creator_items
from app.domains.kol.search_sessions_previews import (
    hydrate_session_item_audience_progress,
)
from app.domains.kol.search_sessions_serde import (
    _int_or_none,
    _normalize_status,
)


TEAM_STATUS_SCHEMA = "kol_search_team_status_v1"
logger = get_logger(__name__)
MAX_TEAM_STATUS_BATCH_SIZE = 1000
# Backward-compatible exported name used by older callers and tests.
MAX_TEAM_STATUS_SESSIONS = MAX_TEAM_STATUS_BATCH_SIZE
MIN_TEAM_STATUS_QUERY_BATCH = 50
MAX_TEAM_STATUS_QUERY_BATCH = 250
MAX_TEAM_STATUS_SCAN_SESSIONS = 50_000
MAX_TEAM_STATUS_SCAN_ITEMS = 250_000
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
    return max(1, min(parsed, MAX_TEAM_STATUS_BATCH_SIZE))


def _query_batch_size(requested_limit: int) -> int:
    return max(
        MIN_TEAM_STATUS_QUERY_BATCH,
        min(requested_limit, MAX_TEAM_STATUS_QUERY_BATCH),
    )


def _safe_scan_cap(value: Any) -> int:
    try:
        parsed = int(value or MAX_TEAM_STATUS_SCAN_SESSIONS)
    except (TypeError, ValueError):
        parsed = MAX_TEAM_STATUS_SCAN_SESSIONS
    return max(1, min(parsed, MAX_TEAM_STATUS_SCAN_SESSIONS))


def _safe_item_scan_cap(value: Any) -> int:
    try:
        parsed = int(value or MAX_TEAM_STATUS_SCAN_ITEMS)
    except (TypeError, ValueError):
        parsed = MAX_TEAM_STATUS_SCAN_ITEMS
    return max(1, min(parsed, MAX_TEAM_STATUS_SCAN_ITEMS))


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


def _default_hydrate_progress(conn: Any, items: list[dict[str, Any]]) -> None:
    hydrate_session_item_audience_progress(conn, items, logger=logger)


def _default_organization_guard(staff: dict[str, Any] | None, conn: Any) -> int:
    # Search sessions predate organization_id.  They are legacy org-1 data and
    # must fail closed for every other workspace until an additive tenant
    # column/backfill exists.
    return scope.assert_legacy_default_organization(
        staff,
        conn,
        feature="KOL search team status",
    )


def _begin_team_status_snapshot(conn: Any) -> None:
    """Pin every PostgreSQL projection read to one read-only MVCC snapshot."""

    if not is_postgres_runtime():
        return
    try:
        # This must be the first statement on the lazily opened request
        # connection.  It prevents archive/restore, item edits and queue-state
        # transitions from producing a mixed READ COMMITTED closure claim.
        conn.execute(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
        )
    except Exception as exc:
        raise RuntimeError("KOL search team status snapshot unavailable") from exc


def _status_object_sql(path: str) -> str:
    """Return a PII-free JSONB status projection for one payload object."""

    return f"""
        jsonb_strip_nulls(jsonb_build_object(
            'status', item.payload_json #> '{{{path},status}}',
            'queue_status', item.payload_json #> '{{{path},queue_status}}',
            'job_id', item.payload_json #> '{{{path},job_id}}',
            'kol_pool_id', item.payload_json #> '{{{path},kol_pool_id}}',
            'profile_data', CASE
                WHEN jsonb_typeof(item.payload_json #> '{{{path},profile_data}}') = 'object'
                 AND item.payload_json #> '{{{path},profile_data}}' <> '{{}}'::jsonb
                THEN '{{"present":true}}'::jsonb
                ELSE NULL
            END,
            'audience_enrichment', jsonb_strip_nulls(jsonb_build_object(
                'status', item.payload_json #> '{{{path},audience_enrichment,status}}',
                'queue_status', item.payload_json #> '{{{path},audience_enrichment,queue_status}}',
                'job_id', item.payload_json #> '{{{path},audience_enrichment,job_id}}'
            ))
        ))
    """


def _downstream_status_sql(role: str) -> str:
    """Project only execution state and presence of durable job identity."""

    job_ids_path = f"{{downstream_jobs,{role},job_ids}}"
    return f"""
        jsonb_strip_nulls(jsonb_build_object(
            'state', item.payload_json #> '{{downstream_jobs,{role},state}}',
            'job_id', item.payload_json #> '{{downstream_jobs,{role},job_id}}',
            'job_ids', CASE
                WHEN jsonb_typeof(item.payload_json #> '{job_ids_path}') = 'array'
                 AND jsonb_array_length(item.payload_json #> '{job_ids_path}') > 0
                THEN '[1]'::jsonb
                ELSE NULL
            END
        ))
    """


_PROGRESS_PAYLOAD_SQL = f"""
    jsonb_strip_nulls(jsonb_build_object(
        'profile_flow', {_status_object_sql('profile_flow')},
        'profile_execute', {_status_object_sql('profile_execute')},
        'profile_advance_job', jsonb_strip_nulls(jsonb_build_object(
            'status', item.payload_json #> '{{profile_advance_job,status}}',
            'queue_status', item.payload_json #> '{{profile_advance_job,queue_status}}',
            'job_id', item.payload_json #> '{{profile_advance_job,job_id}}',
            'id', item.payload_json #> '{{profile_advance_job,id}}'
        )),
        'downstream_jobs', jsonb_build_object(
            'video', {_downstream_status_sql('video')},
            'comments', {_downstream_status_sql('comments')},
            'audience', {_downstream_status_sql('audience')}
        ),
        'audience_preview', jsonb_strip_nulls(jsonb_build_object(
            'status', item.payload_json #> '{{audience_preview,status}}'
        )),
        'video_flow', jsonb_strip_nulls(jsonb_build_object(
            'evidence_id', item.payload_json #> '{{video_flow,evidence_id}}'
        )),
        'analysis', CASE
            WHEN jsonb_typeof(item.payload_json #> '{{analysis}}') = 'object'
             AND item.payload_json #> '{{analysis}}' <> '{{}}'::jsonb
            THEN '{{"present":true}}'::jsonb
            ELSE NULL
        END
    ))
"""


def _minimal_session(row: Any) -> dict[str, Any]:
    raw = dict(row)
    summary = _loads(raw.get("progress_summary_json"), {})
    return {
        "id": raw.get("id"),
        "status": raw.get("status"),
        "created_by": raw.get("created_by"),
        "result_summary": summary if isinstance(summary, dict) else {},
    }


def _minimal_item(row: Any) -> dict[str, Any]:
    raw = dict(row)
    payload = _loads(raw.get("progress_payload_json"), {})
    return {
        "id": raw.get("id"),
        "session_id": raw.get("session_id"),
        "dedupe_key": "",
        "item_type": raw.get("item_type"),
        "status": raw.get("status"),
        "stage": raw.get("stage"),
        "rank": raw.get("rank"),
        "kol_pool_id": raw.get("kol_pool_id"),
        "evidence_id": raw.get("evidence_id"),
        "job_id": raw.get("job_id"),
        "payload": payload if isinstance(payload, dict) else {},
    }


def _session_batch(
    conn: Any,
    *,
    snapshot_max_id: int,
    before_id: int | None,
    batch_size: int,
) -> list[dict[str, Any]]:
    cursor_clause = "" if before_id is None else "AND id < ?"
    params: list[int] = [snapshot_max_id]
    if before_id is not None:
        params.append(before_id)
    params.append(batch_size)
    rows = conn.execute(
        f"""
        SELECT id,
               status,
               created_by,
               jsonb_strip_nulls(jsonb_build_object(
                   'phase', result_summary_json->'phase',
                   'progress', jsonb_strip_nulls(jsonb_build_object(
                       'total', result_summary_json #> '{{progress,total}}',
                       'base', result_summary_json #> '{{progress,base}}',
                       'requested_tasks_terminal',
                           result_summary_json #> '{{progress,requested_tasks_terminal}}'
                   ))
               )) AS progress_summary_json
        FROM vkpi_kol_search_sessions
        WHERE archived_at IS NULL
          AND id <= ?
          {cursor_clause}
        ORDER BY id DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    return [_minimal_session(row) for row in rows]


def _item_counts_by_session(
    conn: Any,
    session_ids: list[int],
    *,
    snapshot_max_item_id: int,
) -> dict[int, int]:
    if not session_ids:
        return {}
    placeholders = ", ".join(["?"] * len(session_ids))
    rows = conn.execute(
        f"""
        SELECT session_id, COUNT(*) AS item_count
        FROM vkpi_kol_search_session_items
        WHERE id <= ?
          AND session_id IN ({placeholders})
        GROUP BY session_id
        """,
        (snapshot_max_item_id, *session_ids),
    ).fetchall()
    return {
        int(session_id): _safe_int(dict(row).get("item_count"))
        for row in rows
        if (session_id := _int_or_none(dict(row).get("session_id")))
    }


def _items_by_session(
    conn: Any,
    session_ids: list[int],
    *,
    snapshot_max_item_id: int,
) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {
        session_id: [] for session_id in session_ids
    }
    if not session_ids:
        return grouped
    placeholders = ", ".join(["?"] * len(session_ids))
    rows = conn.execute(
        f"""
        SELECT item.id,
               item.session_id,
               item.item_type,
               item.status,
               item.stage,
               item.rank,
               item.kol_pool_id,
               item.evidence_id,
               item.job_id,
               {_PROGRESS_PAYLOAD_SQL} AS progress_payload_json
        FROM vkpi_kol_search_session_items AS item
        WHERE item.id <= ?
          AND item.session_id IN ({placeholders})
        ORDER BY item.session_id, item.rank NULLS LAST, item.id
        """,
        (snapshot_max_item_id, *session_ids),
    ).fetchall()
    for row in rows:
        item = _minimal_item(row)
        session_id = _int_or_none(item.get("session_id"))
        if session_id in grouped:
            grouped[int(session_id)].append(item)
    return grouped


def build_team_search_status(
    *,
    staff: dict[str, Any] | None,
    limit: int = MAX_TEAM_STATUS_SESSIONS,
    get_conn_fn: GetConn | None = None,
    project_progress_fn: ProgressProjector = project_search_progress,
    observe_worker_fn: WorkerObserver = observe_worker_health,
    refresh_queue_states_fn: ItemsMutator = _default_refresh,
    hydrate_progress_fn: ItemsMutator = _default_hydrate_progress,
    canonicalize_items_fn: ItemsCanonicalizer = canonicalize_session_creator_items,
    organization_guard_fn: OrganizationGuard = _default_organization_guard,
    max_scan_sessions: int = MAX_TEAM_STATUS_SCAN_SESSIONS,
    max_scan_items: int = MAX_TEAM_STATUS_SCAN_ITEMS,
) -> dict[str, Any]:
    """Return a bounded, PII-free aggregate over unarchived search sessions.

    The effective terminal decision comes from ``project_search_progress`` and
    its ``requested_tasks_terminal`` flag, not from the stored session status.
    This keeps the manager view aligned with employee history/detail views when
    a durable queue row has advanced beyond a stale stored snapshot.
    """

    safe_limit = _safe_limit(limit)
    query_batch_size = _query_batch_size(safe_limit)
    scan_cap = _safe_scan_cap(max_scan_sessions)
    item_scan_cap = _safe_item_scan_cap(max_scan_items)
    conn = (get_conn_fn or get_conn)()
    _begin_team_status_snapshot(conn)
    organization_id = int(organization_guard_fn(staff, conn))
    population_row = conn.execute(
        """
        SELECT COUNT(*) AS session_count,
               COUNT(DISTINCT created_by) AS staff_count,
               MAX(id) AS max_session_id
        FROM vkpi_kol_search_sessions
        WHERE archived_at IS NULL
        """
    ).fetchone()
    population = _safe_int(dict(population_row or {}).get("session_count"))
    staff_population = _safe_int(dict(population_row or {}).get("staff_count"))
    snapshot_max_id = _safe_int(dict(population_row or {}).get("max_session_id"))
    item_population_row = conn.execute(
        """
        SELECT COUNT(*) AS item_count,
               MAX(item.id) AS max_item_id
        FROM vkpi_kol_search_session_items AS item
        JOIN vkpi_kol_search_sessions AS session ON session.id = item.session_id
        WHERE session.archived_at IS NULL
          AND session.id <= ?
        """,
        (snapshot_max_id,),
    ).fetchone()
    item_population = _safe_int(dict(item_population_row or {}).get("item_count"))
    snapshot_max_item_id = _safe_int(
        dict(item_population_row or {}).get("max_item_id")
    )
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
    evaluated = 0
    evaluated_items = 0
    batches = 0
    before_id: int | None = None
    scan_target = min(population, scan_cap)
    item_budget_exhausted = False

    while evaluated < scan_target:
        requested_batch = min(query_batch_size, scan_target - evaluated)
        sessions = _session_batch(
            conn,
            snapshot_max_id=snapshot_max_id,
            before_id=before_id,
            batch_size=requested_batch,
        )
        if not sessions:
            break
        session_ids = [
            int(session_id)
            for session_id in (
                _int_or_none(session.get("id")) for session in sessions
            )
            if session_id
        ]
        if (
            len(session_ids) != len(sessions)
            or len(session_ids) != len(set(session_ids))
            or session_ids != sorted(session_ids, reverse=True)
            or (before_id is not None and session_ids[0] >= before_id)
        ):
            raise RuntimeError("KOL search team status keyset contract violated")

        item_counts = _item_counts_by_session(
            conn,
            session_ids,
            snapshot_max_item_id=snapshot_max_item_id,
        )
        eligible_count = 0
        batch_item_count = 0
        for session_id in session_ids:
            next_item_count = item_counts.get(session_id, 0)
            if evaluated_items + batch_item_count + next_item_count > item_scan_cap:
                item_budget_exhausted = True
                break
            eligible_count += 1
            batch_item_count += next_item_count
        if eligible_count <= 0:
            break
        if eligible_count < len(sessions):
            sessions = sessions[:eligible_count]
            session_ids = session_ids[:eligible_count]

        grouped = _items_by_session(
            conn,
            session_ids,
            snapshot_max_item_id=snapshot_max_item_id,
        )
        all_items = [item for items in grouped.values() for item in items]
        if evaluated_items + len(all_items) > item_scan_cap:
            raise RuntimeError("KOL search team status item scan budget violated")
        if all_items:
            refresh_queue_states_fn(conn, all_items)
            hydrate_progress_fn(conn, all_items)

        for session in sessions:
            session_id = _int_or_none(session.get("id")) or 0
            items = canonicalize_items_fn(grouped.get(session_id, []))
            progress = project_progress_fn(session, items, worker_health=worker)
            effective_state = str(progress.get("state") or "unknown").strip().lower()
            if effective_state not in by_effective_state:
                effective_state = "unknown"
            by_effective_state[effective_state] += 1

            stored_status = _normalize_status(session.get("status"))
            stored_bucket = stored_status if stored_status in by_stored_status else "planned"
            by_stored_status[stored_bucket] += 1

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

        evaluated += len(sessions)
        evaluated_items += len(all_items)
        batches += 1
        next_before_id = session_ids[-1]
        if next_before_id <= 0 or next_before_id == before_id:
            raise RuntimeError("KOL search team status keyset did not advance")
        before_id = next_before_id
        if item_budget_exhausted or len(sessions) < requested_batch:
            break

    # READ COMMITTED can otherwise let an archive/restore race change the
    # active membership between the opening COUNT and the final keyset page.
    # Re-read both membership fences and fail closed instead of claiming that
    # every *current* session is terminal from a mixed population snapshot.
    final_population_row = conn.execute(
        """
        SELECT COUNT(*) AS session_count,
               COUNT(DISTINCT created_by) AS staff_count,
               MAX(id) AS max_session_id
        FROM vkpi_kol_search_sessions
        WHERE archived_at IS NULL
        """
    ).fetchone()
    final_item_population_row = conn.execute(
        """
        SELECT COUNT(*) AS item_count,
               MAX(item.id) AS max_item_id
        FROM vkpi_kol_search_session_items AS item
        JOIN vkpi_kol_search_sessions AS session ON session.id = item.session_id
        WHERE session.archived_at IS NULL
          AND session.id <= ?
        """,
        (snapshot_max_id,),
    ).fetchone()
    snapshot_consistent = (
        _safe_int(dict(final_population_row or {}).get("session_count")) == population
        and _safe_int(dict(final_population_row or {}).get("staff_count"))
        == staff_population
        and _safe_int(dict(final_population_row or {}).get("max_session_id"))
        == snapshot_max_id
        and _safe_int(dict(final_item_population_row or {}).get("item_count"))
        == item_population
        and _safe_int(dict(final_item_population_row or {}).get("max_item_id"))
        == snapshot_max_item_id
    )
    session_coverage_complete = evaluated == population and snapshot_consistent
    item_coverage_complete = evaluated_items == item_population and snapshot_consistent
    coverage_complete = session_coverage_complete and item_coverage_complete
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
            "population": population,
            "evaluated": evaluated,
            "session_population": population,
            "staff_population": staff_population,
            "evaluated_sessions": evaluated,
            "unevaluated_sessions": max(0, population - evaluated),
            "session_complete": session_coverage_complete,
            "session_truncated": not session_coverage_complete,
            "item_population": item_population,
            "evaluated_items": evaluated_items,
            "unevaluated_items": max(0, item_population - evaluated_items),
            "item_scan_cap": item_scan_cap,
            "items_complete": item_coverage_complete,
            "items_truncated": not item_coverage_complete,
            "limit": safe_limit,
            "batch_size": query_batch_size,
            "batches": batches,
            "scan_cap": scan_cap,
            "snapshot_consistent": snapshot_consistent,
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
            "vkpi_kol_pool",
            "apify_jobs",
            "vkpi_worker_heartbeat",
        ],
    }


__all__ = [
    "MAX_TEAM_STATUS_BATCH_SIZE",
    "MAX_TEAM_STATUS_SCAN_ITEMS",
    "MAX_TEAM_STATUS_SCAN_SESSIONS",
    "MAX_TEAM_STATUS_SESSIONS",
    "TEAM_STATUS_SCHEMA",
    "build_team_search_status",
]
