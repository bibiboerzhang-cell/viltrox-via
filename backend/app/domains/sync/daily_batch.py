"""Durable batch lifecycle for the asynchronous daily sync."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any


SUCCESS_STATUSES = frozenset({"done", "completed", "succeeded", "success"})
PARTIAL_STATUSES = frozenset({"partial", "partial_done"})
KNOWN_SKIP_STATUSES = frozenset({"skipped", "skipped_known_reason"})
FAILED_STATUSES = frozenset(
    {"failed", "error", "prefilter_rejected", "cancelled", "canceled", "timeout", "timed_out", "dead_letter"}
)
TERMINAL_STATUSES = SUCCESS_STATUSES | PARTIAL_STATUSES | FAILED_STATUSES | KNOWN_SKIP_STATUSES
PLANNED_PARENT_STALE_SECONDS = 900.0
DETACHED_PARENT_MAX_SECONDS = 21_600.0
PARENT_LOCK_KEY = "vkpi:daily_incremental_sync:durable_batch"
PARENT_PAYLOAD_KEYS = frozenset(
    {
        "official_max_posts", "official_platforms", "skip_official", "channel_max_posts", "max_posts",
        "kol_limit", "kol_offset", "kol_stale_before", "kol_max_posts", "kol_error_stop_threshold",
        "kol_platforms", "kol_refresh_selector", "kol_source_type", "kol_tiers", "skip_kol",
        "allow_legacy_kol_full_refresh", "allow_qualified_kol_refresh",
        "completion_wait_seconds", "completion_poll_seconds",
    }
)


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ActiveDailyBatchError(RuntimeError):
    """A different durable daily parent still owns the orchestration lane."""


def new_batch_id() -> str:
    return f"daily-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}"


def kol_refresh_allowed(daily_sync: Any, payload: dict[str, Any], selector: str) -> bool:
    legacy = daily_sync._bool(payload.get("allow_legacy_kol_full_refresh")) or daily_sync._bool(payload.get("include_legacy_kol"))
    qualified = daily_sync._bool(payload.get("allow_qualified_kol_refresh")) or daily_sync._bool(payload.get("include_qualified_kol"))
    return not daily_sync._bool(payload.get("skip_kol")) and (
        (selector == "qualified" and qualified) or (selector != "qualified" and legacy)
    )


def kol_rows(daily_sync: Any, refresh_tier: Any, payload: dict[str, Any], selector: str) -> list[dict[str, Any]]:
    common = {
        "limit": max(1, min(1200, int(payload.get("kol_limit") or 1200))),
        "offset": max(0, int(payload.get("kol_offset") or 0)),
        "stale_before": str(payload.get("kol_stale_before") or ""),
        "platforms": daily_sync._platform_filter(payload.get("kol_platforms") or payload.get("platforms")),
    }
    if selector == "qualified":
        return refresh_tier.qualified_refresh_rows(
            **common, tiers=daily_sync._tier_filter(payload.get("kol_tiers") or payload.get("refresh_tiers"))
        )
    return daily_sync._kol_light_rows(
        **common, source_type=str(payload.get("kol_source_type") or "legacy_excel_p2d")
    )


def schedule(
    official_rows: list[dict[str, Any]],
    kol_rows: list[dict[str, Any]],
) -> list[tuple[str, dict[str, Any]]]:
    """Strict round robin: KOL starts after the first official, not after all officials."""
    scheduled: list[tuple[str, dict[str, Any]]] = []
    for index in range(max(len(official_rows), len(kol_rows))):
        if index < len(official_rows):
            scheduled.append(("official", official_rows[index]))
        if index < len(kol_rows):
            scheduled.append(("kol_pool_light", kol_rows[index]))
    return scheduled


async def queue_batch(
    official_rows: list[dict[str, Any]],
    kol_rows: list[dict[str, Any]],
    *,
    payload: dict[str, Any],
    staff: dict[str, Any] | None,
    queue: Any,
    batch_id: str,
    progress_callback: Any | None = None,
) -> dict[str, Any]:
    """Enqueue children in fair order; provider work remains worker-only."""
    if queue is None:
        raise RuntimeError("durable job queue unavailable")
    if str(getattr(queue, "backend_name", "")) == "inprocess":
        raise RuntimeError("durable_queue_required:inprocess_queue_has_no_provider_execution_fence")
    import app.domains.tasks.enqueue as task_enqueue

    official_max = int(payload.get("official_max_posts") or payload.get("channel_max_posts") or payload.get("max_posts") or 50)
    kol_max = max(1, min(3, int(payload.get("kol_max_posts") or payload.get("max_posts") or 1)))
    enqueue_staff = staff or {"id": 0, "staff_id": 0, "user_id": 0, "role": "admin", "is_owner": 1, "email": ""}
    ids: dict[str, list[str]] = {"official": [], "kol_pool_light": []}
    failures: dict[str, list[dict[str, Any]]] = {"official": [], "kol_pool_light": []}
    ordered_ids: list[str] = []
    task_links: list[dict[str, Any]] = []
    processed = 0

    def receipt() -> dict[str, Any]:
        official_ids = list(dict.fromkeys(ids["official"]))
        kol_ids = list(dict.fromkeys(ids["kol_pool_light"]))
        return {
            "official": {
                "channels_enqueued": len(official_ids), "channels_requested": len(official_rows),
                "channels_failed_to_enqueue": len(failures["official"]), "task_ids": official_ids,
                "failed": list(failures["official"][:20]),
            },
            "kol_pool_light": {
                "requested": len(kol_rows), "enqueued": len(kol_ids),
                "failed_to_enqueue": len(failures["kol_pool_light"]), "task_ids": kol_ids,
                "failed": list(failures["kol_pool_light"][:20]),
            },
            "task_ids": list(dict.fromkeys(ordered_ids)),
            "task_links": [dict(link) for link in task_links],
            "scheduler": "round_robin_v1",
            "processed": processed,
            "total": len(official_rows) + len(kol_rows),
        }

    for target_index, (lane, row) in enumerate(schedule(official_rows, kol_rows)):
        key = "channel_id" if lane == "official" else "kol_pool_id"
        target_id = int(row.get("id") or 0)
        if target_id <= 0:
            failures[lane].append({key: target_id, "error": "ValueError: positive target id required"})
        else:
            if lane == "official":
                task_type = task_enqueue.VKPI_OFFICIAL_CHANNEL_SYNC
                params = {"channel_id": target_id, "max_posts": int(row.get("_requested_max_posts") or official_max)}
            else:
                task_type = task_enqueue.VKPI_KOL_POOL_ON_DEMAND_REFRESH
                params = {"kol_pool_id": target_id, "reason": "daily_incremental_sync", "max_posts": kol_max}
            try:
                queued = await task_enqueue.enqueue_vkpi_task(
                    queue,
                    task_type,
                    {**params, "orchestration_batch_id": batch_id, "orchestration_lane": lane},
                    staff=enqueue_staff,
                    priority=5,
                )
                task_id = str(queued.get("task_id") or "").strip()
                if not task_id:
                    raise RuntimeError("enqueue returned an empty task_id")
                ids[lane].append(task_id)
                ordered_ids.append(task_id)
                task_links.append({"task_id": task_id, "lane": lane, key: target_id, "target_index": target_index})
            except Exception as exc:
                failures[lane].append({key: target_id, "error": f"{type(exc).__name__}: {str(exc)[:300]}"})
        processed += 1
        current = receipt()
        if callable(progress_callback):
            progress_result = progress_callback(current)
            if hasattr(progress_result, "__await__"):
                await progress_result
    return receipt()


def checkpoint_summary(
    batch_id: str,
    requested: int,
    queued: dict[str, Any],
    *,
    official_skipped: bool = False,
    kol_skipped: bool = False,
    phase: str = "children_enqueued",
) -> dict[str, Any]:
    """Build the recoverable parent receipt from a cumulative enqueue snapshot."""
    task_ids = list(queued.get("task_ids") or [])
    batch = {
        "batch_id": batch_id,
        "parent_persisted": True,
        "identity_scope": "durable_parent_and_task_links",
        "scheduler": str(queued.get("scheduler") or "round_robin_v1"),
        "requested": int(requested or 0),
        "enqueued": len(task_ids),
        "task_ids": task_ids,
        "task_links": list(queued.get("task_links") or []),
    }
    official = {"skipped": True} if official_skipped else queued.get("official") or {}
    kol = {"skipped": True} if kol_skipped else queued.get("kol_pool_light") or {}
    enqueue_failures = int(official.get("channels_failed_to_enqueue") or 0) + int(kol.get("failed_to_enqueue") or 0)
    return {
        "phase": phase,
        "processed": int(queued.get("processed") or (requested if phase == "children_enqueued" else 0)),
        "total": int(queued.get("total") or requested),
        "batch": batch, "official": official,
        "kol_pool_light": kol, "enqueue_failures": enqueue_failures,
    }


def completion_snapshot(
    task_ids: list[str],
    statuses: dict[str, str],
    *,
    wait_seconds: float,
    poll_seconds: float,
    scope: str,
    sla_expired: bool,
    lookup_errors: dict[str, str] | None = None,
) -> dict[str, Any]:
    counts = Counter(statuses.get(task_id, "unobserved") for task_id in task_ids)
    succeeded = sum(counts.get(status, 0) for status in SUCCESS_STATUSES)
    partial = sum(counts.get(status, 0) for status in PARTIAL_STATUSES)
    failed = sum(counts.get(status, 0) for status in FAILED_STATUSES)
    skipped = sum(counts.get(status, 0) for status in KNOWN_SKIP_STATUSES)
    terminal = succeeded + partial + failed + skipped
    pending = max(0, len(task_ids) - terminal)
    complete = bool(task_ids) and pending == 0
    provider = (
        "completed" if complete and not partial and not failed
        else "failed" if complete and failed and not succeeded and not partial
        else "partial" if partial or failed
        else "unknown"
    )
    return {
        "complete": complete, "completion_scope": scope, "provider_completion": provider,
        "wait_seconds": wait_seconds, "poll_seconds": poll_seconds, "sla_expired": sla_expired,
        "tasks_total": len(task_ids), "tasks_terminal": terminal, "tasks_succeeded": succeeded,
        "tasks_partial": partial, "tasks_failed": failed, "tasks_skipped_known": skipped,
        "tasks_pending": pending,
        "status_counts": dict(sorted(counts.items())),
        "task_statuses": {task_id: statuses.get(task_id, "unobserved") for task_id in task_ids},
        "lookup_errors": dict(list((lookup_errors or {}).items())[:20]),
    }


async def observe(
    queue: Any,
    task_ids: list[str],
    *,
    wait_seconds: float,
    poll_seconds: float,
    sleep: Any | None = None,
    monotonic: Any | None = None,
) -> dict[str, Any]:
    """Read child ledger states until terminal or the bounded SLA expires."""
    ids = list(dict.fromkeys(str(item).strip() for item in task_ids if str(item).strip()))
    wait_seconds, poll_seconds = max(0.0, float(wait_seconds or 0)), max(0.05, float(poll_seconds or 1))
    statuses: dict[str, str] = {}
    errors: dict[str, str] = {}
    scope, expired = ("no_work", False) if not ids else ("enqueue_only", False)
    if ids and wait_seconds > 0:
        getter = getattr(queue, "get_status", None)
        if not callable(getter):
            scope, expired, errors = "bounded_observation_unavailable", True, {"queue": "get_status unavailable"}
        else:
            sleeper, clock = sleep or asyncio.sleep, monotonic or time.monotonic
            deadline = clock() + wait_seconds
            while True:
                for task_id in ids:
                    if statuses.get(task_id) in TERMINAL_STATUSES:
                        continue
                    try:
                        row = await getter(task_id)
                        statuses[task_id] = str((row or {}).get("status") or "unknown").strip().lower()
                        errors.pop(task_id, None)
                    except Exception as exc:
                        statuses[task_id] = "lookup_error"
                        errors[task_id] = f"{type(exc).__name__}: {str(exc)[:200]}"
                if all(statuses.get(task_id) in TERMINAL_STATUSES for task_id in ids):
                    scope = "provider_terminal"
                    break
                remaining = deadline - clock()
                if remaining <= 0:
                    scope, expired = "bounded_observation", True
                    break
                await sleeper(min(poll_seconds, remaining))
    result = completion_snapshot(
        ids, statuses, wait_seconds=wait_seconds, poll_seconds=poll_seconds,
        scope=scope, sla_expired=expired, lookup_errors=errors,
    )
    if not ids:
        result.update({"complete": True, "provider_completion": "not_run"})
    return result


def result_status(completion: dict[str, Any], *, enqueue_failures: int) -> str:
    if enqueue_failures or int(completion.get("tasks_partial") or 0) or int(completion.get("tasks_failed") or 0):
        return "partial"
    if not int(completion.get("tasks_total") or 0):
        return "completed"
    if bool(completion.get("complete")):
        return "completed"
    return "queued"


def last_known_target_index(summary: dict[str, Any]) -> int:
    """Return the greatest zero-based target index with known useful progress."""
    batch = summary.get("batch") if isinstance(summary.get("batch"), dict) else {}
    completion = summary.get("completion") if isinstance(summary.get("completion"), dict) else {}
    task_ids = list(batch.get("task_ids") or [])
    indexes = {str(task_id): index for index, task_id in enumerate(task_ids)}
    for link in batch.get("task_links") or []:
        if not isinstance(link, dict):
            continue
        task_id = str(link.get("task_id") or "")
        try:
            target_index = int(link.get("target_index"))
        except (TypeError, ValueError):
            continue
        if task_id and target_index >= 0:
            indexes[task_id] = target_index
    progress = SUCCESS_STATUSES | PARTIAL_STATUSES | KNOWN_SKIP_STATUSES
    statuses = completion.get("task_statuses") if isinstance(completion.get("task_statuses"), dict) else {}
    known = [indexes[task_id] for task_id, status in statuses.items() if status in progress and task_id in indexes]
    return max(known, default=0)


def parent_payload(
    payload: dict[str, Any],
    official_rows: list[dict[str, Any]],
    kol_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Allowlist parent inputs; callbacks, staff objects, tokens and secrets never persist."""
    return {
        "parameters": {key: payload.get(key) for key in sorted(PARENT_PAYLOAD_KEYS) if key in payload},
        "official_target_ids": [int(row.get("id") or 0) for row in official_rows],
        "kol_target_ids": [int(row.get("id") or 0) for row in kol_rows],
    }


def _execute(sql: str, params: tuple[Any, ...], write: Any | None = None) -> int:
    """Execute a parent mutation and expose rowcount instead of silently succeeding."""
    if write is not None:
        result = write(sql, params)
        return int(result if result is not None else 0)
    from app.db.connection import close_standalone_conn, open_standalone_conn

    conn = open_standalone_conn()
    try:
        cursor = conn.execute(sql, params)
        conn.commit()
        return int(getattr(cursor, "rowcount", 0) or 0)
    finally:
        close_standalone_conn(conn)


def _insert_exclusive(sql: str, params: tuple[Any, ...], write: Any | None = None) -> int:
    """Atomically reject overlapping running parents before an insert-only create."""
    if write is not None:
        result = write(sql, params)
        return int(result if result is not None else 0)
    from app.db.connection import close_standalone_conn, is_postgres_runtime, open_standalone_conn

    conn = open_standalone_conn()
    try:
        if is_postgres_runtime():
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (PARENT_LOCK_KEY,))
        elif isinstance(conn, sqlite3.Connection):
            conn.execute("BEGIN IMMEDIATE")
        active = conn.execute(
            """SELECT run_id FROM vkpi_sync_runs
               WHERE job_name=? AND stage=? AND status='running' LIMIT 1""",
            ("daily_incremental_sync", "durable_batch"),
        ).fetchone()
        if active:
            active_id = str(dict(active).get("run_id") or "")
            raise ActiveDailyBatchError(f"daily_batch_parent_active:{active_id}")
        cursor = conn.execute(sql, params)
        conn.commit()
        return int(getattr(cursor, "rowcount", 0) or 0)
    except Exception:
        conn.rollback()
        raise
    finally:
        close_standalone_conn(conn)


def parent_status(batch_id: str, *, read: Any | None = None) -> str:
    if read is not None:
        row = read(batch_id)
        return str((row or {}).get("status") or "")
    from app.db.connection import close_standalone_conn, open_standalone_conn

    conn = open_standalone_conn()
    try:
        row = conn.execute("SELECT status FROM vkpi_sync_runs WHERE run_id=?", (batch_id,)).fetchone()
        return str(dict(row).get("status") or "") if row else ""
    finally:
        close_standalone_conn(conn)


def load_running_parents(limit: int = 20) -> list[dict[str, Any]]:
    """Read recent detached parents for bounded reconciliation on the next run."""
    from app.db.connection import close_standalone_conn, open_standalone_conn

    conn = open_standalone_conn()
    try:
        rows = conn.execute(
            """SELECT run_id, started_at, updated_at, summary_json FROM vkpi_sync_runs
               WHERE job_name=? AND stage=? AND status='running'
               ORDER BY started_at DESC LIMIT ?""",
            ("daily_incremental_sync", "durable_batch", max(1, min(100, int(limit or 20)))),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        close_standalone_conn(conn)


def _validated_parent_receipt(row: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    try:
        summary = json.loads(str(row.get("summary_json") or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_parent_summary") from exc
    if not isinstance(summary, dict):
        raise ValueError("invalid_parent_summary_shape")
    batch = summary.get("batch") if isinstance(summary.get("batch"), dict) else {}
    raw_task_ids = batch.get("task_ids") or []
    if not isinstance(raw_task_ids, list):
        raise ValueError("invalid_parent_task_ids_shape")
    if any(not isinstance(item, str) or not item.strip() for item in raw_task_ids):
        raise ValueError("invalid_parent_task_id")
    return summary, list(dict.fromkeys(item.strip() for item in raw_task_ids))


def _parent_age_seconds(row: dict[str, Any], anchor: datetime, default: float) -> float:
    refreshed_at = row.get("updated_at") or row.get("started_at")
    if isinstance(refreshed_at, str):
        try:
            refreshed_at = datetime.fromisoformat(refreshed_at.replace("Z", "+00:00"))
        except ValueError:
            refreshed_at = None
    if not isinstance(refreshed_at, datetime):
        return float(default)
    if refreshed_at.tzinfo is None:
        refreshed_at = refreshed_at.replace(tzinfo=timezone.utc)
    return max(0.0, (anchor - refreshed_at.astimezone(timezone.utc)).total_seconds())


def _mark_parent_failed(
    run_id: str,
    exc: BaseException,
    *,
    write: Any | None,
    read: Any | None,
) -> str:
    fail_parent(run_id, exc, write=write, read=read)
    return "failed"


def _apply_reconciled_completion(summary: dict[str, Any], completion: dict[str, Any]) -> str:
    summary["completion"] = completion
    status = result_status(
        completion, enqueue_failures=int(summary.get("enqueue_failures") or 0)
    )
    summary.update({
        "status": status, "completion_scope": completion["completion_scope"],
        "provider_completion": completion["provider_completion"],
    })
    return status


async def _read_child_completion(queue: Any, task_ids: list[str]) -> dict[str, Any]:
    statuses: dict[str, str] = {}
    for task_id in task_ids:
        try:
            status_row = await queue.get_status(task_id)
            statuses[task_id] = str((status_row or {}).get("status") or "unknown").lower()
        except Exception:
            statuses[task_id] = "lookup_error"
    scope = (
        "provider_terminal"
        if all(value in TERMINAL_STATUSES for value in statuses.values())
        else "reconcile_pending"
    )
    return completion_snapshot(
        task_ids, statuses, wait_seconds=0, poll_seconds=0,
        scope=scope, sla_expired=False,
    )


async def _reconcile_parent(
    row: dict[str, Any],
    queue: Any,
    *,
    write: Any | None,
    read: Any | None,
    anchor: datetime,
    planned_stale_seconds: float,
    detached_max_seconds: float,
) -> str:
    run_id = str(row.get("run_id") or "")
    try:
        summary, task_ids = _validated_parent_receipt(row)
    except ValueError as exc:
        return _mark_parent_failed(run_id, exc, write=write, read=read)
    phase = str(summary.get("phase") or "")
    if phase in {"planned", "enqueueing"}:
        age = _parent_age_seconds(row, anchor, planned_stale_seconds)
        if age < max(0.0, float(planned_stale_seconds)):
            return "pending"
        message = "enqueue_progress_interrupted" if phase == "enqueueing" else "planned_parent_checkpoint_missing"
        return _mark_parent_failed(run_id, TimeoutError(message), write=write, read=read)
    if phase != "children_enqueued":
        return _mark_parent_failed(run_id, ValueError("invalid_parent_phase"), write=write, read=read)
    if not task_ids:
        completion = completion_snapshot(
            [], {}, wait_seconds=0, poll_seconds=0,
            scope="no_work", sla_expired=False,
        )
        completion.update({"complete": True, "provider_completion": "not_run"})
        status = _apply_reconciled_completion(summary, completion)
        finish_parent(run_id, status, summary, write=write, read=read)
        return "reconciled"
    completion = await _read_child_completion(queue, task_ids)
    if not completion["complete"]:
        age = _parent_age_seconds(row, anchor, detached_max_seconds)
        if age >= max(0.0, float(detached_max_seconds)):
            return _mark_parent_failed(
                run_id, TimeoutError("child_completion_lifecycle_exceeded"),
                write=write, read=read,
            )
        return "pending"
    status = _apply_reconciled_completion(summary, completion)
    finish_parent(run_id, status, summary, write=write, read=read)
    return "reconciled"


async def reconcile_recent_parents(
    queue: Any,
    *,
    load: Any | None = None,
    write: Any | None = None,
    read: Any | None = None,
    now: datetime | None = None,
    planned_stale_seconds: float = PLANNED_PARENT_STALE_SECONDS,
    detached_max_seconds: float = DETACHED_PARENT_MAX_SECONDS,
) -> dict[str, int]:
    """Finish detached parents once every linked child has reached a terminal state."""
    rows = (load or load_running_parents)()
    anchor = now or datetime.now(timezone.utc)
    outcomes = Counter()
    checked = 0
    for row in rows:
        checked += 1
        outcomes[await _reconcile_parent(
            row, queue, write=write, read=read, anchor=anchor,
            planned_stale_seconds=planned_stale_seconds,
            detached_max_seconds=detached_max_seconds,
        )] += 1
    return {
        "checked": checked,
        "reconciled": outcomes["reconciled"],
        "pending": outcomes["pending"],
        "failed": outcomes["failed"],
    }


def insert_parent(
    batch_id: str,
    payload: dict[str, Any],
    official_rows: list[dict[str, Any]],
    kol_rows: list[dict[str, Any]],
    *,
    write: Any | None = None,
) -> None:
    """Create the parent exactly once. A conflict fails before any child enqueue."""
    now = utcnow()
    sql = """
        INSERT INTO vkpi_sync_runs
          (run_id, job_name, stage, started_at, status, total_targets, last_success_index,
           payload_json, summary_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    params = (
        batch_id, "daily_incremental_sync", "durable_batch", now, "running",
        len(official_rows) + len(kol_rows), 0,
        json.dumps(parent_payload(payload, official_rows, kol_rows), ensure_ascii=False, default=str),
        json.dumps({"phase": "planned", "batch_id": batch_id}, ensure_ascii=False), now,
    )
    try:
        if _insert_exclusive(sql, params, write) != 1:
            raise RuntimeError("insert affected no parent row")
    except Exception as exc:
        raise RuntimeError(f"daily_batch_parent_insert_failed:{batch_id}") from exc


def checkpoint_parent(batch_id: str, summary: dict[str, Any], *, write: Any | None = None) -> None:
    """Persist child links without changing or reviving a terminal parent."""
    reason = {
        "planned": "parent_planned",
        "enqueueing": "children_enqueueing",
        "children_enqueued": "children_enqueued",
    }.get(str(summary.get("phase") or ""))
    if reason is None:
        raise ValueError("daily_batch_parent_checkpoint_phase_invalid")
    changed = _execute(
        """UPDATE vkpi_sync_runs SET reason=?, summary_json=?, updated_at=?
           WHERE run_id=? AND status='running'""",
        (reason, json.dumps(summary, ensure_ascii=False, default=str), utcnow(), batch_id),
        write,
    )
    if changed != 1:
        raise RuntimeError(f"daily_batch_parent_checkpoint_rejected:{batch_id}")


def fail_parent(
    batch_id: str,
    exc: BaseException,
    *,
    write: Any | None = None,
    read: Any | None = None,
) -> None:
    """Fail an inserted parent when orchestration itself cannot continue."""
    now = utcnow()
    changed = _execute(
        """UPDATE vkpi_sync_runs SET finished_at=?, status='failed', reason=?, error_type='other',
           error_class=?, error_message=?, updated_at=? WHERE run_id=? AND status='running'""",
        (now, "orchestration_failed", type(exc).__name__, str(exc)[:500], now, batch_id),
        write,
    )
    if changed != 1 and parent_status(batch_id, read=read) != "failed":
        raise RuntimeError(f"daily_batch_parent_fail_rejected:{batch_id}")


def finish_parent(
    batch_id: str,
    status: str,
    summary: dict[str, Any],
    *,
    write: Any | None = None,
    read: Any | None = None,
) -> None:
    """Finalize terminal observation or checkpoint an intentionally detached observer."""
    summary = dict(summary)
    batch = summary.get("batch") if isinstance(summary.get("batch"), dict) else {}
    if batch and "phase" not in summary:
        summary.update({
            "phase": "children_enqueued",
            "processed": int(batch.get("requested") or 0),
            "total": int(batch.get("requested") or 0),
        })
    completion = summary.get("completion") if isinstance(summary.get("completion"), dict) else {}
    now = utcnow()
    if status in {"completed", "partial"}:
        task_ids = list(batch.get("task_ids") or [])
        no_work = not task_ids and bool(completion.get("complete")) and completion.get("completion_scope") == "no_work"
        all_terminal = bool(task_ids) and bool(completion.get("complete")) and not int(completion.get("tasks_pending") or 0)
        if not (no_work or all_terminal):
            raise RuntimeError(f"daily_batch_parent_terminal_evidence_required:{batch_id}")
        official = summary.get("official") if isinstance(summary.get("official"), dict) else {}
        kol = summary.get("kol_pool_light") if isinstance(summary.get("kol_pool_light"), dict) else {}
        errors = (
            int(official.get("channels_failed_to_enqueue") or 0)
            + int(kol.get("failed_to_enqueue") or 0)
            + int(completion.get("tasks_failed") or 0)
            + int(completion.get("tasks_partial") or 0)
        )
        requested = int(batch.get("requested") or 0)
        error_rate = float(errors) / float(requested) if requested else 0.0
        status_values = set((completion.get("task_statuses") or {}).values())
        infra_failure = bool(status_values & {"timeout", "timed_out", "dead_letter"})
        ledger_status = "failed" if error_rate > 0.10 or infra_failure else "completed"
        reason = (
            "children_completed" if not errors
            else f"infrastructure_child_failure:{errors}/{requested}"
            if infra_failure
            else f"completed_with_errors:{errors}/{requested}"
            if ledger_status == "completed"
            else f"failure_threshold_exceeded:{errors}/{requested}"
        )
        changed = _execute(
            """UPDATE vkpi_sync_runs SET finished_at=?, status=?, last_success_index=?, reason=?,
               error_type=?, summary_json=?, updated_at=? WHERE run_id=? AND status='running'""",
            (now, ledger_status, last_known_target_index(summary), reason,
             None if ledger_status == "completed" else "other",
             json.dumps(summary, ensure_ascii=False, default=str), now, batch_id),
            write,
        )
        if changed != 1 and parent_status(batch_id, read=read) != ledger_status:
            raise RuntimeError(f"daily_batch_parent_finish_rejected:{batch_id}")
        return
    reason = "completion_sla_expired" if completion.get("sla_expired") else "observer_detached"
    changed = _execute(
        """UPDATE vkpi_sync_runs SET reason=?, last_success_index=?, summary_json=?, updated_at=?
           WHERE run_id=? AND status='running'""",
        (reason, last_known_target_index(summary),
         json.dumps(summary, ensure_ascii=False, default=str), now, batch_id),
        write,
    )
    if changed != 1:
        raise RuntimeError(f"daily_batch_parent_observer_checkpoint_rejected:{batch_id}")
