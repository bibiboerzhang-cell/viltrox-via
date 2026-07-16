"""Product-domain scheduler jobs."""
from __future__ import annotations

from app.core.logging import get_logger

from .jobs_tasks import _record_scheduler_run, _scheduler_task_enabled


logger = get_logger(__name__)
TASK_KEY = "vkpi_official_catalog_sync"


async def job_vkpi_official_catalog_sync() -> dict | None:
    """Sync the free public viltrox.com catalog when its registry gate is enabled."""
    if not _scheduler_task_enabled(TASK_KEY):
        logger.info("scheduler.vkpi_official_catalog_sync_skipped", extra={"reason": "disabled"})
        return
    try:
        from app.domains.products import official_catalog_sync

        logger.info(
            "scheduler.vkpi_official_catalog_sync_started",
            extra={"source_url": official_catalog_sync.OFFICIAL_CATALOG_URL},
        )
        result = await official_catalog_sync.sync_official_catalog()
        logger.info("scheduler.vkpi_official_catalog_sync", extra=result)
        _record_scheduler_run(TASK_KEY, ok=True)
        return result
    except Exception as exc:
        logger.exception(
            "scheduler.vkpi_official_catalog_sync_failed",
            extra={"error_type": getattr(exc, "error_type", "other")},
        )
        _record_scheduler_run(TASK_KEY, ok=False, error=str(exc)[:240])
        raise
