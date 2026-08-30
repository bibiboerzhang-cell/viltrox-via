"""Truthful progress semantics for progressive KOL search sessions.

``complete`` and ``required_tasks_complete`` are retained as compatibility
aliases for "every requested task has reached a terminal state".  They are not
evidence that the optional full-analysis pipeline ran.  The strict
``full_analysis_complete`` flag additionally requires observable, durable data
for every profile/video/comments/audience stage; a finished job alone is not a
completed analysis.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.domains.kol.search_progress_projection import (
    FULL_ANALYSIS_ROLES,
    _aggregate_progress_state,
    _completion_kind,
    _full_analysis_flags,
    _orchestration_pending,
    _progress_unit_totals,
    _project_progress_stages,
    _requested_progress_outcomes,
    _text,
)

PROGRESS_CONTRACT_SCHEMA = "kol_search_progress_v1"
PROGRESS_STAGE_KEYS = ("search", "profile", *FULL_ANALYSIS_ROLES)

_HEARTBEAT_WINDOW_SECONDS = 120
_EXACT_RELEASE_SHA = re.compile(r"^[0-9a-f]{40}$")
_PROJECT_ROOT = Path(__file__).resolve().parents[4]


def _count(stage: Mapping[str, Any], key: str) -> int:
    try:
        return max(0, int(stage.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def completion_contract(
    *,
    base_count: int,
    total: int,
    terminal_count: int,
    ready_count: int,
    profile_failed: int = 0,
    active_tasks: int = 0,
    stage_progress: Mapping[str, Mapping[str, Any]] | None = None,
    requested_tasks_terminal: bool | None = None,
) -> dict[str, bool]:
    """Return backward-compatible and strict progressive-completion flags.

    ``requested_tasks_terminal`` may be supplied by an orchestrator that knows
    more work will be registered after the current profile batch.  Otherwise it
    is derived from terminal item counts and active tasks.
    """

    safe_total = max(0, int(total or 0))
    safe_base = max(0, int(base_count or 0))
    safe_terminal = max(0, int(terminal_count or 0))
    safe_ready = max(0, int(ready_count or 0))
    safe_profile_failed = max(0, int(profile_failed or 0))
    safe_active = max(0, int(active_tasks or 0))

    base_complete = safe_total > 0 and safe_base >= safe_total
    terminal = (
        bool(requested_tasks_terminal)
        if requested_tasks_terminal is not None
        else safe_total > 0 and safe_terminal >= safe_total and safe_active == 0
    )

    stages = stage_progress if isinstance(stage_progress, Mapping) else None
    full_analysis_execution_complete = bool(
        terminal
        and stages is not None
        and safe_total > 0
        and safe_ready >= safe_total
        and safe_profile_failed == 0
    )
    if full_analysis_execution_complete:
        for role in FULL_ANALYSIS_ROLES:
            raw_stage = stages.get(role)
            if not isinstance(raw_stage, Mapping):
                full_analysis_execution_complete = False
                break
            if (
                _count(raw_stage, "ready") < safe_total
                or _count(raw_stage, "active") > 0
                or _count(raw_stage, "failed") > 0
                or _count(raw_stage, "not_requested") > 0
            ):
                full_analysis_execution_complete = False
                break

    full_analysis_observable = bool(
        stages is not None
        and safe_total > 0
        and all(
            isinstance(stages.get(role), Mapping)
            and stages[role].get("data_ready") is not None
            for role in ("profile", *FULL_ANALYSIS_ROLES)
        )
    )
    full_analysis_complete = bool(
        full_analysis_execution_complete
        and full_analysis_observable
        and all(
            _count(stages[role], "data_ready") >= safe_total
            for role in ("profile", *FULL_ANALYSIS_ROLES)
        )
    )
    decision_eligible = bool(
        full_analysis_complete
        and safe_profile_failed == 0
        and safe_ready >= safe_total
    )
    return {
        "base_complete": base_complete,
        "requested_tasks_terminal": terminal,
        "full_analysis_execution_complete": full_analysis_execution_complete,
        "full_analysis_observable": full_analysis_observable,
        "full_analysis_complete": full_analysis_complete,
        "decision_eligible": decision_eligible,
        # Compatibility aliases.  They intentionally retain terminal—not full
        # analysis—semantics for old clients.
        "required_tasks_complete": terminal,
        "complete": terminal,
    }


def _utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _expected_worker_count() -> int | None:
    raw = str(os.getenv("APIFY_WORKER_EXPECTED_INSTANCES", "") or "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else None


def _exact_release_sha(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized if _EXACT_RELEASE_SHA.fullmatch(normalized) else None


def _observed_app_release_sha() -> tuple[str | None, str]:
    """Read only explicit, sealed release identity; never infer it from git."""

    env_sha = _exact_release_sha(os.getenv("APP_GIT_SHA"))
    if env_sha:
        return env_sha, "env:APP_GIT_SHA"
    try:
        build_sha = _exact_release_sha((_PROJECT_ROOT / "BUILD_GIT_SHA").read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        build_sha = None
    if build_sha:
        return build_sha, "build_file:BUILD_GIT_SHA"
    return None, "unavailable"


def unobserved_worker_health(
    *,
    reason: str = "heartbeat_not_read",
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    now = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    release_sha, release_sha_source = _observed_app_release_sha()
    return {
        "observed": False,
        "source": "vkpi_worker_heartbeat",
        "state": "unknown",
        "online": None,
        "online_count": None,
        "expected_count": _expected_worker_count(),
        "capacity_ready": None,
        "release_sha": release_sha,
        "release_sha_source": release_sha_source,
        "worker_sha": None,
        "worker_shas": [],
        "sha_aligned": None,
        "latest_heartbeat_at": None,
        "observed_at": _iso(now),
        "reason": reason,
    }


def observe_worker_health(
    conn: Any,
    *,
    now: datetime | None = None,
    expected_count: int | None = None,
) -> dict[str, Any]:
    """Read the durable Apify-worker heartbeat table without guessing liveness.

    A missing table/read failure is ``unknown`` rather than ``offline``.  Redis
    workers are excluded because they do not consume the ``apify_jobs`` lanes
    used by KOL profile/video/comment/audience work.
    """

    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expected = expected_count if expected_count is not None else _expected_worker_count()
    release_sha, release_sha_source = _observed_app_release_sha()
    try:
        rows = conn.execute(
            """
            SELECT worker_name, last_heartbeat_at, worker_git_sha
            FROM vkpi_worker_heartbeat
            WHERE last_heartbeat_at IS NOT NULL
              AND worker_name NOT LIKE ?
            ORDER BY last_heartbeat_at DESC
            LIMIT 64
            """,
            ("redis-worker-%",),
        ).fetchall()
    except Exception:
        return {
            "observed": False,
            "source": "vkpi_worker_heartbeat",
            "state": "unknown",
            "online": None,
            "online_count": None,
            "expected_count": expected,
            "capacity_ready": None,
            "release_sha": release_sha,
            "release_sha_source": release_sha_source,
            "worker_sha": None,
            "worker_shas": [],
            "sha_aligned": None,
            "latest_heartbeat_at": None,
            "observed_at": _iso(observed_at),
            "reason": "heartbeat_unavailable",
        }

    parsed_rows: list[dict[str, Any]] = []
    for raw in rows or []:
        row = dict(raw)
        heartbeat = _utc(row.get("last_heartbeat_at"))
        age = (observed_at - heartbeat).total_seconds() if heartbeat else None
        parsed_rows.append(
            {
                "heartbeat": heartbeat,
                "online": bool(age is not None and -30 <= age <= _HEARTBEAT_WINDOW_SECONDS),
                "worker_sha": _exact_release_sha(row.get("worker_git_sha")),
            }
        )
    online_rows = [row for row in parsed_rows if row["online"]]
    online_count = len(online_rows)
    worker_shas = sorted(
        {str(row["worker_sha"]) for row in online_rows if row.get("worker_sha")}
    )
    worker_sha = worker_shas[0] if len(worker_shas) == 1 else None
    sha_aligned: bool | None = None
    if release_sha and online_rows:
        sha_aligned = bool(
            len(worker_shas) == 1
            and worker_sha == release_sha
            and all(row.get("worker_sha") == release_sha for row in online_rows)
        )
    latest = max((row["heartbeat"] for row in parsed_rows if row["heartbeat"]), default=None)
    count_ready = bool(online_count > 0 and (expected is None or online_count >= expected))
    capacity_ready = bool(count_ready and sha_aligned is not False)
    if online_count <= 0:
        state = "offline"
    elif expected is not None and online_count < expected:
        state = "under_capacity"
    elif sha_aligned is False:
        state = "release_mismatch"
    else:
        state = "online"
    reason = {
        "offline": "no_fresh_apify_worker_heartbeat",
        "under_capacity": "worker_count_below_expected",
        "release_mismatch": "worker_release_sha_mismatch",
        "online": "fresh_heartbeat",
    }[state]
    return {
        "observed": True,
        "source": "vkpi_worker_heartbeat",
        "state": state,
        "online": bool(online_count > 0),
        "online_count": online_count,
        "expected_count": expected,
        "capacity_ready": capacity_ready,
        "release_sha": release_sha,
        "release_sha_source": release_sha_source,
        "worker_sha": worker_sha,
        "worker_shas": worker_shas,
        "sha_aligned": sha_aligned,
        "latest_heartbeat_at": _iso(latest),
        "observed_at": _iso(observed_at),
        "reason": reason,
    }


def _progress_contract_result(
    session: Mapping[str, Any],
    summary: Mapping[str, Any],
    *,
    state: str,
    unit_totals: tuple[int, int, int, int, int, int, int],
    requested_tasks_terminal: bool,
    requested_tasks_successful: bool,
    completion_kind: str,
    not_requested_stages: list[str],
    empty_result: bool,
    orchestration_pending: bool,
    stages: Mapping[str, Mapping[str, Any]],
    worker: Mapping[str, Any],
    blocked_by_worker: bool,
    full_analysis_flags: tuple[bool, bool, bool],
    observed_at: datetime | None,
) -> dict[str, Any]:
    (
        requested_units,
        successful_units,
        terminal_units,
        queued_units,
        running_units,
        active_units,
        failed_units,
    ) = unit_totals
    (
        full_analysis_execution_complete,
        full_analysis_observable,
        full_analysis_complete,
    ) = full_analysis_flags
    return {
        "schema": PROGRESS_CONTRACT_SCHEMA,
        "claim_status": "observed_execution_only",
        "state": state,
        "session_status": _text(session.get("status")) or "planned",
        "phase": _text(summary.get("phase")) or None,
        "requested_units": requested_units,
        "successful_units": successful_units,
        "terminal_units": terminal_units,
        "queued_units": queued_units,
        "running_units": running_units,
        "active_units": active_units,
        "failed_units": failed_units,
        "requested_tasks_terminal": requested_tasks_terminal,
        "requested_tasks_successful": requested_tasks_successful,
        "completion_kind": completion_kind,
        "not_requested_stages": not_requested_stages,
        "empty_result": empty_result,
        "orchestration_pending": orchestration_pending,
        "orchestration_pending_basis": (
            "session_running_and_orchestrator_declares_more_tasks" if orchestration_pending else None
        ),
        "progress_pct": round(successful_units * 100 / requested_units, 1) if requested_units else 0.0,
        "terminal_pct": round(terminal_units * 100 / requested_units, 1) if requested_units else 0.0,
        "progress_pct_basis": "durable_success_only; queued_running_active_failed_not_counted_as_success",
        "stages": stages,
        "worker": worker,
        "blocked_by_worker": blocked_by_worker,
        "full_analysis_execution_complete": full_analysis_execution_complete,
        "full_analysis_observable": full_analysis_observable,
        "full_analysis_complete": full_analysis_complete,
        "observed_at": _iso((observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)),
        "sources": [
            "vkpi_kol_search_sessions.result_summary_json",
            "vkpi_kol_search_session_items.payload_json",
            "vkpi_worker_heartbeat",
        ],
    }


def project_search_progress(
    session: Mapping[str, Any],
    items: Sequence[Mapping[str, Any]],
    *,
    worker_health: Mapping[str, Any] | None = None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Project one read-only, evidence-based progress contract.

    Queue creation is visible as ``queued`` but contributes zero to
    ``successful`` and ``progress_pct``.  Failed/partial work contributes to
    terminal progress only, never to successful progress.
    """

    summary, stored_progress, safe_items, raw_session_status, stages = (
        _project_progress_stages(session, items)
    )
    unit_totals = _progress_unit_totals(stages)
    (
        requested_units,
        successful_units,
        terminal_units,
        queued_units,
        running_units,
        active_units,
        failed_units,
    ) = unit_totals
    worker = (
        dict(worker_health)
        if isinstance(worker_health, Mapping)
        else unobserved_worker_health(observed_at=observed_at)
    )
    orchestration_pending = _orchestration_pending(session, stored_progress)
    active_units_total = queued_units + running_units + active_units
    requested_tasks_terminal, requested_tasks_successful = _requested_progress_outcomes(
        orchestration_pending=orchestration_pending,
        active_units_total=active_units_total,
        requested_units=requested_units,
        terminal_units=terminal_units,
        successful_units=successful_units,
        failed_units=failed_units,
        raw_session_status=raw_session_status,
    )
    not_requested_stages = [
        key
        for key in ("profile", *FULL_ANALYSIS_ROLES)
        if stages[key]["state"] == "not_requested"
    ]
    blocked_by_worker = bool(
        worker.get("observed") is True
        and worker.get("online") is False
        and (queued_units > 0 or running_units > 0 or active_units > 0 or orchestration_pending)
    )
    state = _aggregate_progress_state(
        session,
        blocked_by_worker=blocked_by_worker,
        running_units=running_units,
        orchestration_pending=orchestration_pending,
        active_units=active_units,
        queued_units=queued_units,
        failed_units=failed_units,
        requested_units=requested_units,
        successful_units=successful_units,
        raw_session_status=raw_session_status,
    )
    full_analysis_flags = _full_analysis_flags(stages, item_count=len(safe_items))
    full_analysis_complete = full_analysis_flags[2]
    empty_result = bool(
        not safe_items
        and requested_tasks_terminal
        and raw_session_status in {"ready", "partial"}
    )
    completion_kind = _completion_kind(
        blocked_by_worker=blocked_by_worker,
        orchestration_pending=orchestration_pending,
        active_units_total=active_units_total,
        full_analysis_complete=full_analysis_complete,
        empty_result=empty_result,
        requested_tasks_successful=requested_tasks_successful,
        requested_tasks_terminal=requested_tasks_terminal,
    )

    return _progress_contract_result(
        session,
        summary,
        state=state,
        unit_totals=unit_totals,
        requested_tasks_terminal=requested_tasks_terminal,
        requested_tasks_successful=requested_tasks_successful,
        completion_kind=completion_kind,
        not_requested_stages=not_requested_stages,
        empty_result=empty_result,
        orchestration_pending=orchestration_pending,
        stages=stages,
        worker=worker,
        blocked_by_worker=blocked_by_worker,
        full_analysis_flags=full_analysis_flags,
        observed_at=observed_at,
    )


__all__ = [
    "FULL_ANALYSIS_ROLES",
    "PROGRESS_CONTRACT_SCHEMA",
    "PROGRESS_STAGE_KEYS",
    "completion_contract",
    "observe_worker_health",
    "project_search_progress",
    "unobserved_worker_health",
]
