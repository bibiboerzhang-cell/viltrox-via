"""KOL scheduler jobs kept behind explicit registry gates."""
from __future__ import annotations

import asyncio

from app.core.logging import get_logger

from .jobs_tasks import _record_scheduler_run, _scheduler_task_enabled
from app.services.scheduler_result_contract import normalize_scheduler_result, scheduler_dispatch_result


logger = get_logger(__name__)
TASK_KEY = "vkpi_kol_video_metric_refresh"
CONTENT_MONITOR_TASK_KEY = "vkpi_kol_content_monitoring"


async def job_vkpi_kol_video_metric_refresh() -> dict | None:
    """Queue due tracked-video refreshes; the scheduler never calls providers."""

    if not _scheduler_task_enabled(TASK_KEY):
        logger.info(
            "scheduler.vkpi_kol_video_metric_refresh_skipped",
            extra={"reason": "disabled"},
        )
        return None
    try:
        from app.domains.kol import video_metric_schedule

        result = await asyncio.to_thread(
            video_metric_schedule.enqueue_due_tracked_video_refreshes
        )
        result = scheduler_dispatch_result(result)
        outcome = normalize_scheduler_result(result)
        _record_scheduler_run(TASK_KEY, ok=outcome.ok, error=outcome.error, status=outcome.registry_status)
        logger.info(
            "scheduler.vkpi_kol_video_metric_refresh",
            extra={
                "status": result.get("status"),
                "candidates_scanned": result.get("candidates_scanned"),
                "due_selected": result.get("due_selected"),
                "queued": result.get("queued"),
                "already_queued": result.get("already_queued"),
                "paused": result.get("paused"),
                "failed": result.get("failed"),
                "scan_truncated": result.get("scan_truncated"),
            },
        )
        return result
    except Exception as exc:
        error_code = type(exc).__name__.lower()[:80] or "scheduler_error"
        logger.error(
            "scheduler.vkpi_kol_video_metric_refresh_failed",
            extra={"error_code": error_code},
        )
        _record_scheduler_run(TASK_KEY, ok=False, error=error_code)
        return {
            "status": "failed",
            "error_code": error_code,
            "provider_calls_performed": False,
        }


async def job_vkpi_kol_content_monitoring() -> dict | None:
    """Queue explicit recent-content subscriptions; never call a provider here."""

    if not _scheduler_task_enabled(CONTENT_MONITOR_TASK_KEY):
        logger.info("scheduler.vkpi_kol_content_monitoring_skipped", extra={"reason": "disabled"})
        return None
    from app.core.release_validation import release_validation_active

    if release_validation_active():
        return {
            "status": "blocked",
            "reason": "release_validation_fenced",
            "provider_calls_performed": False,
        }
    try:
        from app.domains.kol import content_monitoring

        result = await asyncio.to_thread(content_monitoring.enqueue_due_content_monitoring)
        result = scheduler_dispatch_result(result)
        outcome = normalize_scheduler_result(result)
        _record_scheduler_run(CONTENT_MONITOR_TASK_KEY, ok=outcome.ok, error=outcome.error, status=outcome.registry_status)
        logger.info(
            "scheduler.vkpi_kol_content_monitoring",
            extra={
                key: result.get(key)
                for key in (
                    "status",
                    "candidates_scanned",
                    "due_selected",
                    "queued",
                    "already_queued",
                    "paused",
                    "failed",
                    "scan_truncated",
                )
            },
        )
        return result
    except Exception as exc:
        error_code = type(exc).__name__.lower()[:80] or "scheduler_error"
        _record_scheduler_run(CONTENT_MONITOR_TASK_KEY, ok=False, error=error_code)
        return {
            "status": "failed",
            "error_code": error_code,
            "provider_calls_performed": False,
        }


__all__ = [
    "CONTENT_MONITOR_TASK_KEY",
    "TASK_KEY",
    "job_vkpi_kol_content_monitoring",
    "job_vkpi_kol_video_metric_refresh",
]
