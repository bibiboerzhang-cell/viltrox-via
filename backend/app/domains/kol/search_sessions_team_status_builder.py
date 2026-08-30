"""Bounded scan and response projection for management team-search status."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class TeamStatusRuntime:
    safe_int: Callable[[Any], int]
    int_or_none: Callable[[Any], int | None]
    normalize_status: Callable[[Any], str]
    iso_now: Callable[[], str]
    release_evidence: Callable[[dict[str, Any]], dict[str, Any]]
    session_batch: Callable[..., list[dict[str, Any]]]
    item_counts_by_session: Callable[..., dict[int, int]]
    items_by_session: Callable[..., dict[int, list[dict[str, Any]]]]
    schema: str
    effective_states: tuple[str, ...]
    stored_statuses: tuple[str, ...]


@dataclass(frozen=True)
class PopulationSnapshot:
    session_count: int
    staff_count: int
    max_session_id: int
    item_count: int
    max_item_id: int


@dataclass
class StatusCounts:
    by_effective_state: dict[str, int]
    nonterminal_by_effective_state: dict[str, int]
    by_stored_status: dict[str, int]
    terminal: int = 0
    nonterminal: int = 0
    blocked: int = 0
    orchestration_pending: int = 0
    full_analysis_complete: int = 0
    nonterminal_staff_ids: set[int] = field(default_factory=set)

    @classmethod
    def create(cls, runtime: TeamStatusRuntime) -> "StatusCounts":
        return cls(
            by_effective_state={state: 0 for state in runtime.effective_states},
            nonterminal_by_effective_state={
                state: 0 for state in runtime.effective_states
            },
            by_stored_status={status: 0 for status in runtime.stored_statuses},
        )

    def observe(
        self,
        session: dict[str, Any],
        progress: dict[str, Any],
        runtime: TeamStatusRuntime,
    ) -> None:
        effective_state = str(progress.get("state") or "unknown").strip().lower()
        if effective_state not in self.by_effective_state:
            effective_state = "unknown"
        self.by_effective_state[effective_state] += 1
        stored_status = runtime.normalize_status(session.get("status"))
        stored_bucket = (
            stored_status
            if stored_status in self.by_stored_status
            else "planned"
        )
        self.by_stored_status[stored_bucket] += 1
        if progress.get("requested_tasks_terminal") is True:
            self.terminal += 1
        else:
            self.nonterminal += 1
            self.nonterminal_by_effective_state[effective_state] += 1
            created_by = runtime.int_or_none(session.get("created_by"))
            if created_by:
                self.nonterminal_staff_ids.add(int(created_by))
        if progress.get("blocked_by_worker") is True:
            self.blocked += 1
        if progress.get("orchestration_pending") is True:
            self.orchestration_pending += 1
        if progress.get("full_analysis_complete") is True:
            self.full_analysis_complete += 1


@dataclass
class ScanState:
    evaluated: int = 0
    evaluated_items: int = 0
    batches: int = 0
    before_id: int | None = None


@dataclass(frozen=True)
class BatchOutcome:
    session_count: int
    item_count: int
    next_before_id: int
    should_stop: bool


def _read_population(
    conn: Any,
    safe_int: Callable[[Any], int],
) -> PopulationSnapshot:
    population_row = conn.execute(
        """
        SELECT COUNT(*) AS session_count,
               COUNT(DISTINCT created_by) AS staff_count,
               MAX(id) AS max_session_id
        FROM vkpi_kol_search_sessions
        WHERE archived_at IS NULL
        """
    ).fetchone()
    population_values = dict(population_row or {})
    session_count = safe_int(population_values.get("session_count"))
    staff_count = safe_int(population_values.get("staff_count"))
    max_session_id = safe_int(population_values.get("max_session_id"))
    item_population_row = conn.execute(
        """
        SELECT COUNT(*) AS item_count,
               MAX(item.id) AS max_item_id
        FROM vkpi_kol_search_session_items AS item
        JOIN vkpi_kol_search_sessions AS session ON session.id = item.session_id
        WHERE session.archived_at IS NULL
          AND session.id <= ?
        """,
        (max_session_id,),
    ).fetchone()
    item_values = dict(item_population_row or {})
    return PopulationSnapshot(
        session_count=session_count,
        staff_count=staff_count,
        max_session_id=max_session_id,
        item_count=safe_int(item_values.get("item_count")),
        max_item_id=safe_int(item_values.get("max_item_id")),
    )


def _session_ids(
    sessions: list[dict[str, Any]],
    *,
    before_id: int | None,
    int_or_none: Callable[[Any], int | None],
) -> list[int]:
    session_ids = [
        int(session_id)
        for session_id in (
            int_or_none(session.get("id")) for session in sessions
        )
        if session_id
    ]
    invalid = (
        len(session_ids) != len(sessions)
        or len(session_ids) != len(set(session_ids))
        or session_ids != sorted(session_ids, reverse=True)
        or (before_id is not None and session_ids[0] >= before_id)
    )
    if invalid:
        raise RuntimeError("KOL search team status keyset contract violated")
    return session_ids


def _eligible_session_count(
    session_ids: list[int],
    item_counts: dict[int, int],
    *,
    evaluated_items: int,
    item_scan_cap: int,
) -> tuple[int, bool]:
    eligible_count = 0
    batch_item_count = 0
    exhausted = False
    for session_id in session_ids:
        next_item_count = item_counts.get(session_id, 0)
        if evaluated_items + batch_item_count + next_item_count > item_scan_cap:
            exhausted = True
            break
        eligible_count += 1
        batch_item_count += next_item_count
    return eligible_count, exhausted


def _project_batch(
    conn: Any,
    sessions: list[dict[str, Any]],
    session_ids: list[int],
    *,
    snapshot: PopulationSnapshot,
    evaluated_items: int,
    item_scan_cap: int,
    worker: dict[str, Any],
    counts: StatusCounts,
    project_progress_fn: Callable[..., dict[str, Any]],
    refresh_queue_states_fn: Callable[[Any, list[dict[str, Any]]], None],
    hydrate_progress_fn: Callable[[Any, list[dict[str, Any]]], None],
    canonicalize_items_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    runtime: TeamStatusRuntime,
) -> int:
    grouped = runtime.items_by_session(
        conn,
        session_ids,
        snapshot_max_item_id=snapshot.max_item_id,
    )
    all_items = [item for items in grouped.values() for item in items]
    if evaluated_items + len(all_items) > item_scan_cap:
        raise RuntimeError("KOL search team status item scan budget violated")
    if all_items:
        refresh_queue_states_fn(conn, all_items)
        hydrate_progress_fn(conn, all_items)
    for session in sessions:
        session_id = runtime.int_or_none(session.get("id")) or 0
        items = canonicalize_items_fn(grouped.get(session_id, []))
        progress = project_progress_fn(session, items, worker_health=worker)
        counts.observe(session, progress, runtime)
    return len(all_items)


def _process_batch(
    conn: Any,
    *,
    requested_batch: int,
    snapshot: PopulationSnapshot,
    state: ScanState,
    item_scan_cap: int,
    worker: dict[str, Any],
    counts: StatusCounts,
    project_progress_fn: Callable[..., dict[str, Any]],
    refresh_queue_states_fn: Callable[[Any, list[dict[str, Any]]], None],
    hydrate_progress_fn: Callable[[Any, list[dict[str, Any]]], None],
    canonicalize_items_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    runtime: TeamStatusRuntime,
) -> BatchOutcome | None:
    sessions = runtime.session_batch(
        conn,
        snapshot_max_id=snapshot.max_session_id,
        before_id=state.before_id,
        batch_size=requested_batch,
    )
    if not sessions:
        return None
    session_ids = _session_ids(
        sessions,
        before_id=state.before_id,
        int_or_none=runtime.int_or_none,
    )
    item_counts = runtime.item_counts_by_session(
        conn,
        session_ids,
        snapshot_max_item_id=snapshot.max_item_id,
    )
    eligible_count, exhausted = _eligible_session_count(
        session_ids,
        item_counts,
        evaluated_items=state.evaluated_items,
        item_scan_cap=item_scan_cap,
    )
    if eligible_count <= 0:
        return None
    if eligible_count < len(sessions):
        sessions = sessions[:eligible_count]
        session_ids = session_ids[:eligible_count]
    item_count = _project_batch(
        conn,
        sessions,
        session_ids,
        snapshot=snapshot,
        evaluated_items=state.evaluated_items,
        item_scan_cap=item_scan_cap,
        worker=worker,
        counts=counts,
        project_progress_fn=project_progress_fn,
        refresh_queue_states_fn=refresh_queue_states_fn,
        hydrate_progress_fn=hydrate_progress_fn,
        canonicalize_items_fn=canonicalize_items_fn,
        runtime=runtime,
    )
    return BatchOutcome(
        session_count=len(sessions),
        item_count=item_count,
        next_before_id=session_ids[-1],
        should_stop=exhausted or len(sessions) < requested_batch,
    )


def _scan_sessions(
    conn: Any,
    *,
    snapshot: PopulationSnapshot,
    query_batch_size: int,
    scan_cap: int,
    item_scan_cap: int,
    worker: dict[str, Any],
    counts: StatusCounts,
    project_progress_fn: Callable[..., dict[str, Any]],
    refresh_queue_states_fn: Callable[[Any, list[dict[str, Any]]], None],
    hydrate_progress_fn: Callable[[Any, list[dict[str, Any]]], None],
    canonicalize_items_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    runtime: TeamStatusRuntime,
) -> ScanState:
    state = ScanState()
    scan_target = min(snapshot.session_count, scan_cap)
    while state.evaluated < scan_target:
        requested_batch = min(query_batch_size, scan_target - state.evaluated)
        outcome = _process_batch(
            conn,
            requested_batch=requested_batch,
            snapshot=snapshot,
            state=state,
            item_scan_cap=item_scan_cap,
            worker=worker,
            counts=counts,
            project_progress_fn=project_progress_fn,
            refresh_queue_states_fn=refresh_queue_states_fn,
            hydrate_progress_fn=hydrate_progress_fn,
            canonicalize_items_fn=canonicalize_items_fn,
            runtime=runtime,
        )
        if outcome is None:
            break
        state.evaluated += outcome.session_count
        state.evaluated_items += outcome.item_count
        state.batches += 1
        if outcome.next_before_id <= 0 or outcome.next_before_id == state.before_id:
            raise RuntimeError("KOL search team status keyset did not advance")
        state.before_id = outcome.next_before_id
        if outcome.should_stop:
            break
    return state


def _nonzero(values: dict[str, int]) -> dict[str, int]:
    return {key: value for key, value in values.items() if value}


def _result(
    *,
    organization_id: int,
    safe_limit: int,
    query_batch_size: int,
    scan_cap: int,
    item_scan_cap: int,
    snapshot: PopulationSnapshot,
    snapshot_consistent: bool,
    worker: dict[str, Any],
    counts: StatusCounts,
    state: ScanState,
    runtime: TeamStatusRuntime,
) -> dict[str, Any]:
    session_complete = state.evaluated == snapshot.session_count and snapshot_consistent
    items_complete = state.evaluated_items == snapshot.item_count and snapshot_consistent
    complete = session_complete and items_complete
    nonzero_effective = _nonzero(counts.by_effective_state)
    nonzero_nonterminal = _nonzero(counts.nonterminal_by_effective_state)
    return {
        "schema": runtime.schema,
        "status": "ready" if complete else "partial",
        "claim_status": "observed_read_only",
        "scope": {
            "mode": "all_staff_in_organization",
            "organization_id": organization_id,
            "management_only": True,
            "archived_sessions_included": False,
        },
        "coverage": {
            "population": snapshot.session_count,
            "evaluated": state.evaluated,
            "session_population": snapshot.session_count,
            "staff_population": snapshot.staff_count,
            "evaluated_sessions": state.evaluated,
            "unevaluated_sessions": max(0, snapshot.session_count - state.evaluated),
            "session_complete": session_complete,
            "session_truncated": not session_complete,
            "item_population": snapshot.item_count,
            "evaluated_items": state.evaluated_items,
            "unevaluated_items": max(0, snapshot.item_count - state.evaluated_items),
            "item_scan_cap": item_scan_cap,
            "items_complete": items_complete,
            "items_truncated": not items_complete,
            "limit": safe_limit,
            "batch_size": query_batch_size,
            "batches": state.batches,
            "scan_cap": scan_cap,
            "snapshot_consistent": snapshot_consistent,
            "complete": complete,
            "truncated": not complete,
        },
        "counts": {
            "sessions_evaluated": state.evaluated,
            "requested_tasks_terminal": counts.terminal,
            "requested_tasks_nonterminal": counts.nonterminal,
            "staff_with_observed_nonterminal_sessions": len(counts.nonterminal_staff_ids),
            "blocked_by_worker": counts.blocked,
            "orchestration_pending": counts.orchestration_pending,
            "full_analysis_complete": counts.full_analysis_complete,
            "by_effective_state": nonzero_effective,
            "by_stored_status": _nonzero(counts.by_stored_status),
        },
        "nonterminal": {
            "observed_count": counts.nonterminal,
            "by_effective_state": nonzero_nonterminal,
            "all_current_sessions_terminal": (
                counts.nonterminal == 0 if complete else None
            ),
        },
        "release_evidence": runtime.release_evidence(worker),
        "observed_at": str(worker.get("observed_at") or runtime.iso_now()),
        "sources": [
            "vkpi_kol_search_sessions",
            "vkpi_kol_search_session_items",
            "vkpi_kol_pool",
            "apify_jobs",
            "vkpi_worker_heartbeat",
        ],
    }


def build_team_search_status(
    *,
    conn: Any,
    organization_id: int,
    safe_limit: int,
    query_batch_size: int,
    scan_cap: int,
    item_scan_cap: int,
    project_progress_fn: Callable[..., dict[str, Any]],
    observe_worker_fn: Callable[[Any], dict[str, Any]],
    refresh_queue_states_fn: Callable[[Any, list[dict[str, Any]]], None],
    hydrate_progress_fn: Callable[[Any, list[dict[str, Any]]], None],
    canonicalize_items_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    runtime: TeamStatusRuntime,
) -> dict[str, Any]:
    snapshot = _read_population(conn, runtime.safe_int)
    worker = observe_worker_fn(conn)
    counts = StatusCounts.create(runtime)
    state = _scan_sessions(
        conn,
        snapshot=snapshot,
        query_batch_size=query_batch_size,
        scan_cap=scan_cap,
        item_scan_cap=item_scan_cap,
        worker=worker,
        counts=counts,
        project_progress_fn=project_progress_fn,
        refresh_queue_states_fn=refresh_queue_states_fn,
        hydrate_progress_fn=hydrate_progress_fn,
        canonicalize_items_fn=canonicalize_items_fn,
        runtime=runtime,
    )
    final_snapshot = _read_population(conn, runtime.safe_int)
    return _result(
        organization_id=organization_id,
        safe_limit=safe_limit,
        query_batch_size=query_batch_size,
        scan_cap=scan_cap,
        item_scan_cap=item_scan_cap,
        snapshot=snapshot,
        snapshot_consistent=final_snapshot == snapshot,
        worker=worker,
        counts=counts,
        state=state,
        runtime=runtime,
    )
