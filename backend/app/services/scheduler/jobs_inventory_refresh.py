"""Queue-only KOL inventory callbacks with truthful completion receipts."""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from app.core.logging import get_logger
from app.services.scheduler_result_contract import normalize_scheduler_result, scheduler_dispatch_result

logger = get_logger(__name__)


async def run_auto_poll(enabled: Callable[..., bool], record: Callable[..., Any], enqueue: Callable[..., Any]):
    if not enabled("kol_auto_poll"):
        return None
    try:
        from app.domains.kol import auto_poll

        res = await asyncio.to_thread(auto_poll.enqueue_auto_poll, None)
        logger.info("scheduler.kol_auto_poll", extra={"status": str(res.get("status")), "enqueued": res.get("enqueued_count")})
        enrich_job_id = await enqueue(
            "kol_apify_enrich_candidates", {"limit": 10, "requested_by": "scheduler"},
            lock_key="kol_apify_enrich_candidates:auto_poll", timeout_seconds=3600,
        )
        result = scheduler_dispatch_result({**res, "queued": 1, "enrichment_job_id": enrich_job_id})
        outcome = normalize_scheduler_result(result)
        record("kol_auto_poll", ok=outcome.ok, error=outcome.error, status=outcome.registry_status)
        return result
    except Exception as exc:
        logger.exception("scheduler.kol_auto_poll_failed")
        record("kol_auto_poll", ok=False, error=str(exc)[:240])
        raise


async def run_profile_refresh(enabled: Callable[..., bool], record: Callable[..., Any]):
    task_key = "kol_profile_incremental_refresh"
    if not enabled(task_key):
        return None
    from app.core.release_validation import release_validation_active

    if release_validation_active():
        record(task_key, ok=False, error="release_validation_fenced", status="blocked")
        return {"status": "blocked", "reason": "release_validation_fenced", "provider_calls_performed": False}
    try:
        from app.domains.kol import search_inventory_refresh

        result = scheduler_dispatch_result(await asyncio.to_thread(search_inventory_refresh.enqueue_daily_refresh))
        outcome = normalize_scheduler_result(result)
        record(task_key, ok=outcome.ok, error=outcome.error, status=outcome.registry_status)
        logger.info("scheduler.kol_profile_incremental_refresh", extra={
            key: result.get(key) for key in ("status", "candidate_count", "queued", "already_queued", "failed")
        })
        return result
    except Exception as exc:
        logger.exception("scheduler.kol_profile_incremental_refresh_failed")
        record(task_key, ok=False, error=str(exc)[:240], status="failed")
        return {"status": "failed", "error_code": type(exc).__name__.lower()[:80], "provider_calls_performed": False}
