"""Cross-process scheduler run-now helpers.

Migration 269 makes the API-to-scheduler handoff persistent, but delivery is
still degraded at-least-once: APScheduler ``next_run_time`` is process-local and
there is not yet a callback acknowledgement/dispatch-lease protocol.  Keep that
boundary explicit and do not describe these helpers as exactly-once delivery.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any, Callable


RUN_REQUEST_TABLE = "vkpi_scheduler_run_requests"
RUN_REQUEST_DISPATCH_MAX = 50
RUN_REQUEST_FEATURE_ENV = "VKPI_SCHEDULER_RUN_NOW_ENABLED"


def run_request_feature_enabled() -> bool:
    """Keep the degraded cross-process handoff opt-in until ACK/lease lands."""

    raw = str(os.environ.get(RUN_REQUEST_FEATURE_ENV, "0") or "0").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off", ""}:
        return False
    raise RuntimeError(f"{RUN_REQUEST_FEATURE_ENV} must be an explicit boolean")


def _disabled_result(job_id: str) -> dict[str, Any]:
    return {
        "job_id": str(job_id or "").strip(),
        "status": "disabled",
        "queued": False,
        "triggered": False,
        "reason": "run_now_ack_lease_not_ready",
        "error": "cross-process scheduler run-now is disabled",
    }


def trigger_job_now(job_id: str, *, scheduler: Any, logger: Any) -> dict[str, Any]:
    """Move a registered APScheduler job's next run time without calling it."""

    clean_job_id = str(job_id or "").strip()
    if scheduler is None or not bool(getattr(scheduler, "running", False)):
        return {
            "job_id": clean_job_id,
            "status": "not_started",
            "triggered": False,
            "error": "scheduler not started in this process",
        }
    job = scheduler.get_job(clean_job_id)
    if job is None:
        return {
            "job_id": clean_job_id,
            "status": "not_found",
            "triggered": False,
            "error": "job not found",
        }
    scheduled_for = datetime.now(timezone.utc)
    try:
        scheduler.modify_job(clean_job_id, next_run_time=scheduled_for)
    except Exception as exc:
        logger.warning(
            "scheduler.run_now_failed",
            extra={"job_id": clean_job_id, "error_type": type(exc).__name__},
        )
        return {
            "job_id": clean_job_id,
            "status": "error",
            "triggered": False,
            "error": "scheduler modify failed",
        }
    return {
        "job_id": clean_job_id,
        "status": "triggered",
        "triggered": True,
        "scheduled_for": scheduled_for.isoformat().replace("+00:00", "Z"),
    }


def storage_status(
    *,
    postgres_runtime: Callable[[], bool],
    table_exists_fn: Callable[[str], bool],
    get_conn_fn: Callable[[], Any],
    logger: Any,
) -> tuple[bool, str]:
    """Return fail-closed migration/storage readiness for the request queue."""

    if not postgres_runtime():
        return False, "postgres_required"
    try:
        available = table_exists_fn(RUN_REQUEST_TABLE)
    except Exception as exc:
        try:
            get_conn_fn().rollback()
        except Exception as rollback_exc:
            logger.debug(
                "scheduler.run_request_storage_check_rollback_failed",
                extra={"error_type": type(rollback_exc).__name__},
            )
        logger.warning(
            "scheduler.run_request_storage_check_failed",
            extra={"error_type": type(exc).__name__},
        )
        return False, "storage_check_failed"
    if not available:
        try:
            get_conn_fn().rollback()
        except Exception as rollback_exc:
            logger.debug(
                "scheduler.run_request_missing_migration_rollback_failed",
                extra={"error_type": type(rollback_exc).__name__},
            )
        return False, "migration_269_not_applied"
    return True, "ready"


def enqueue_job_run_request(
    job_id: str,
    *,
    requested_by: int | None,
    storage_status_fn: Callable[[], tuple[bool, str]],
    get_conn_fn: Callable[[], Any],
    logger: Any,
) -> dict[str, Any]:
    """Persist one bounded request for a separate scheduler process."""

    clean_job_id = str(job_id or "").strip()
    if not run_request_feature_enabled():
        return _disabled_result(clean_job_id)
    if not clean_job_id or len(clean_job_id) > 160:
        return {
            "job_id": clean_job_id,
            "status": "invalid",
            "queued": False,
            "triggered": False,
            "error": "invalid scheduler job id",
        }
    storage_ready, reason = storage_status_fn()
    if not storage_ready:
        return {
            "job_id": clean_job_id,
            "status": "unavailable",
            "queued": False,
            "triggered": False,
            "reason": reason,
            "error": "scheduler run request storage unavailable",
        }
    actor_id: int | None = None
    if requested_by is not None:
        try:
            parsed_actor_id = int(requested_by)
        except (TypeError, ValueError):
            parsed_actor_id = 0
        actor_id = parsed_actor_id if parsed_actor_id > 0 else None
    conn = get_conn_fn()
    try:
        row = conn.execute(
            """
            INSERT INTO vkpi_scheduler_run_requests (
                task_key, requested_by, status, created_at, updated_at
            ) VALUES (?, ?, 'queued', NOW(), NOW())
            ON CONFLICT (task_key) WHERE status = 'queued'
            DO UPDATE SET task_key = EXCLUDED.task_key
            RETURNING id, task_key, status, created_at
            """,
            (clean_job_id, actor_id),
        ).fetchone()
        if row is None:
            raise RuntimeError("scheduler run request insert returned no row")
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception as rollback_exc:
            logger.debug(
                "scheduler.run_request_enqueue_rollback_failed",
                extra={"error_type": type(rollback_exc).__name__},
            )
        logger.warning(
            "scheduler.run_request_enqueue_failed",
            extra={"job_id": clean_job_id, "error_type": type(exc).__name__},
        )
        return {
            "job_id": clean_job_id,
            "status": "unavailable",
            "queued": False,
            "triggered": False,
            "reason": "storage_write_failed",
            "error": "scheduler run request unavailable",
        }
    created_at = row["created_at"]
    queued_at = (
        created_at.isoformat().replace("+00:00", "Z")
        if hasattr(created_at, "isoformat")
        else str(created_at or "")
    )
    return {
        "job_id": str(row["task_key"]),
        "request_id": int(row["id"]),
        "status": "queued",
        "queued": True,
        "triggered": False,
        "queued_at": queued_at,
    }


def dispatch_queued_run_requests(
    *,
    limit: int,
    scheduler: Any,
    storage_status_fn: Callable[[], tuple[bool, str]],
    get_conn_fn: Callable[[], Any],
    trigger_fn: Callable[[str], dict[str, Any]],
    logger: Any,
) -> dict[str, Any]:
    """Dispatch a bounded batch with explicit degraded at-least-once semantics."""

    if not run_request_feature_enabled():
        return {
            "status": "disabled",
            "reason": "run_now_ack_lease_not_ready",
            "claimed": 0,
            "dispatched": 0,
            "errors": 0,
        }
    if scheduler is None or not bool(getattr(scheduler, "running", False)):
        return {"status": "not_started", "claimed": 0, "dispatched": 0, "errors": 0}
    storage_ready, reason = storage_status_fn()
    if not storage_ready:
        return {
            "status": "unavailable",
            "reason": reason,
            "claimed": 0,
            "dispatched": 0,
            "errors": 0,
        }
    bounded_limit = max(1, min(RUN_REQUEST_DISPATCH_MAX, int(limit or 1)))
    claimed = dispatched = errors = 0
    conn = get_conn_fn()
    for _ in range(bounded_limit):
        try:
            row = conn.execute(
                """
                SELECT id, task_key
                FROM vkpi_scheduler_run_requests
                WHERE status = 'queued'
                ORDER BY created_at ASC, id ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                conn.commit()
                break
            request_id = int(row["id"])
            task_key = str(row["task_key"])
            claim_cursor = conn.execute(
                """
                UPDATE vkpi_scheduler_run_requests
                SET claimed_at = NOW(), updated_at = NOW()
                WHERE id = ? AND status = 'queued'
                """,
                (request_id,),
            )
            if int(claim_cursor.rowcount or 0) != 1:
                conn.rollback()
                continue
            result = trigger_fn(task_key)
            result_status = str(result.get("status") or "error")
            if result_status == "not_started":
                conn.rollback()
                return {
                    "status": "not_started",
                    "claimed": claimed,
                    "dispatched": dispatched,
                    "errors": errors,
                }
            request_status = "dispatched" if bool(result.get("triggered")) else "error"
            error_code = "" if request_status == "dispatched" else f"dispatch_{result_status}"
            conn.execute(
                """
                UPDATE vkpi_scheduler_run_requests
                SET status = ?,
                    dispatched_at = CASE WHEN ? = 'dispatched' THEN NOW() ELSE NULL END,
                    error = ?,
                    updated_at = NOW()
                WHERE id = ? AND status = 'queued'
                """,
                (request_status, request_status, error_code, request_id),
            )
            conn.commit()
            claimed += 1
            if request_status == "dispatched":
                dispatched += 1
            else:
                errors += 1
        except Exception as exc:
            try:
                conn.rollback()
            except Exception as rollback_exc:
                logger.debug(
                    "scheduler.run_request_dispatch_rollback_failed",
                    extra={"error_type": type(rollback_exc).__name__},
                )
            logger.warning(
                "scheduler.run_request_dispatch_failed",
                extra={"error_type": type(exc).__name__},
            )
            return {
                "status": "error",
                "claimed": claimed,
                "dispatched": dispatched,
                "errors": errors,
                "error": "scheduler run request dispatch failed",
            }
    return {"status": "ok", "claimed": claimed, "dispatched": dispatched, "errors": errors}


def scheduler_status(
    *,
    scheduler: Any,
    fleet: dict[str, Any],
    available: bool,
    task_allowlist: list[str],
) -> dict[str, Any]:
    if scheduler is None:
        return {"running": False, "jobs": [], "available": available, "fleet": fleet}
    jobs = [
        {
            "id": job.id,
            "name": job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
        }
        for job in scheduler.get_jobs()
    ]
    return {
        "running": scheduler.running,
        "jobs": jobs,
        "available": available,
        "task_allowlist": task_allowlist,
        "fleet": fleet,
    }


# Thin compatibility facade: jobs.py remains the public monkeypatch surface for
# existing tests and callers, while this sibling owns the implementation.
def trigger_from_jobs_module(module: Any, job_id: str) -> dict[str, Any]:
    return trigger_job_now(job_id, scheduler=module._scheduler, logger=module.logger)


def storage_from_jobs_module(module: Any) -> tuple[bool, str]:
    return storage_status(
        postgres_runtime=module.is_postgres_runtime,
        table_exists_fn=module.table_exists,
        get_conn_fn=module.get_conn,
        logger=module.logger,
    )


def enqueue_from_jobs_module(
    module: Any,
    job_id: str,
    requested_by: int | None,
) -> dict[str, Any]:
    return enqueue_job_run_request(
        job_id,
        requested_by=requested_by,
        storage_status_fn=module._scheduler_run_request_storage_status,
        get_conn_fn=module.get_conn,
        logger=module.logger,
    )


def dispatch_from_jobs_module(module: Any, limit: int) -> dict[str, Any]:
    return dispatch_queued_run_requests(
        limit=limit,
        scheduler=module._scheduler,
        storage_status_fn=module._scheduler_run_request_storage_status,
        get_conn_fn=module.get_conn,
        trigger_fn=module.trigger_job_now,
        logger=module.logger,
    )


def status_from_jobs_module(module: Any) -> dict[str, Any]:
    fleet = module._fleet_controller.status() if module._fleet_controller is not None else {
        "identity": module._SCHEDULER_INSTANCE_ID,
        "is_leader": False,
        "backend": "not_started",
    }
    return scheduler_status(
        scheduler=module._scheduler,
        fleet=fleet,
        available=module._APSCHEDULER_AVAILABLE,
        task_allowlist=sorted(module.scheduler_task_allowlist() or ()),
    )
