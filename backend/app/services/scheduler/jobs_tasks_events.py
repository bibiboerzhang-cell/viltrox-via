"""Event/Dealer scheduler jobs kept behind explicit registry gates."""
from __future__ import annotations

import asyncio
import os

from app.core.logging import get_logger

from .jobs_tasks import _record_scheduler_run, _scheduler_task_enabled


logger = get_logger(__name__)
TASK_KEY = "vkpi_dealer_activity_candidate_sync"


async def job_vkpi_dealer_activity_candidate_sync() -> dict | None:
    """Fetch approved Dealer feeds into review-only candidate staging.

    Migration 261 registers this task disabled.  Enabling the scheduler task is
    necessary but not sufficient: each source must independently pass the
    active/enabled, legal-review, source-passport and exact-feed gates.
    """
    if not _scheduler_task_enabled(TASK_KEY):
        logger.info(
            "scheduler.vkpi_dealer_activity_candidate_sync_skipped",
            extra={"reason": "disabled"},
        )
        return None
    organization_id = str(
        os.getenv("VKPI_DEALER_ACTIVITY_SYNC_ORGANIZATION_ID") or ""
    ).strip()
    if not organization_id.isdecimal() or int(organization_id) <= 0:
        error = "explicit_organization_id_required"
        _record_scheduler_run(TASK_KEY, ok=False, error=error)
        logger.error(
            "scheduler.vkpi_dealer_activity_candidate_sync_blocked",
            extra={"reason": error},
        )
        return {
            "status": "blocked",
            "reason": error,
            "sources_claimed": 0,
            "candidate_rows_written": 0,
            "business_rows_written": 0,
            "network_accessed": False,
        }
    try:
        from app.domains.events import dealer_activity_sync

        result = await asyncio.to_thread(
            dealer_activity_sync.run_dealer_activity_candidate_sync,
            organization_id=int(organization_id),
            allow_network=True,
        )
        ok = str(result.get("status") or "") in {"ok", "empty"}
        error = "" if ok else (
            f"status={result.get('status')};"
            f"failed_or_partial={result.get('sources_failed_or_partial', 0)}"
        )
        _record_scheduler_run(TASK_KEY, ok=ok, error=error[:240])
        logger.info(
            "scheduler.vkpi_dealer_activity_candidate_sync",
            extra={
                "status": result.get("status"),
                "sources_claimed": result.get("sources_claimed"),
                "candidate_rows_written": result.get("candidate_rows_written"),
                "business_rows_written": 0,
            },
        )
        return result
    except Exception as exc:
        logger.exception("scheduler.vkpi_dealer_activity_candidate_sync_failed")
        _record_scheduler_run(TASK_KEY, ok=False, error=str(exc)[:240])
        raise


__all__ = ["TASK_KEY", "job_vkpi_dealer_activity_candidate_sync"]
