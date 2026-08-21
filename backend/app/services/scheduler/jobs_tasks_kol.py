"""KOL scheduler jobs kept behind explicit registry gates."""
from __future__ import annotations

import asyncio

from app.core.logging import get_logger

from .jobs_tasks import _record_scheduler_run, _scheduler_task_enabled


logger = get_logger(__name__)
TASK_KEY = "vkpi_kol_video_metric_refresh"


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
        ok = str(result.get("status") or "") in {"ok", "empty"}
        error = "" if ok else f"candidate_failures={int(result.get('failed') or 0)}"
        _record_scheduler_run(TASK_KEY, ok=ok, error=error)
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


__all__ = ["TASK_KEY", "job_vkpi_kol_video_metric_refresh"]
