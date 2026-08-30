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
from app.domains.kol import search_sessions_team_status_builder


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
    """Return a bounded, PII-free aggregate over unarchived search sessions."""

    safe_limit = _safe_limit(limit)
    conn = (get_conn_fn or get_conn)()
    _begin_team_status_snapshot(conn)
    organization_id = int(organization_guard_fn(staff, conn))
    return search_sessions_team_status_builder.build_team_search_status(
        conn=conn,
        organization_id=organization_id,
        safe_limit=safe_limit,
        query_batch_size=_query_batch_size(safe_limit),
        scan_cap=_safe_scan_cap(max_scan_sessions),
        item_scan_cap=_safe_item_scan_cap(max_scan_items),
        project_progress_fn=project_progress_fn,
        observe_worker_fn=observe_worker_fn,
        refresh_queue_states_fn=refresh_queue_states_fn,
        hydrate_progress_fn=hydrate_progress_fn,
        canonicalize_items_fn=canonicalize_items_fn,
        runtime=search_sessions_team_status_builder.TeamStatusRuntime(
            safe_int=_safe_int,
            int_or_none=_int_or_none,
            normalize_status=_normalize_status,
            iso_now=_iso_now,
            release_evidence=_release_evidence,
            session_batch=_session_batch,
            item_counts_by_session=_item_counts_by_session,
            items_by_session=_items_by_session,
            schema=TEAM_STATUS_SCHEMA,
            effective_states=_EFFECTIVE_STATES,
            stored_statuses=_STORED_STATUSES,
        ),
    )


__all__ = [
    "MAX_TEAM_STATUS_BATCH_SIZE",
    "MAX_TEAM_STATUS_SCAN_ITEMS",
    "MAX_TEAM_STATUS_SCAN_SESSIONS",
    "MAX_TEAM_STATUS_SESSIONS",
    "TEAM_STATUS_SCHEMA",
    "build_team_search_status",
]
