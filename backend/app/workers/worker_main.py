"""
workers/worker_main.py — dedicated Redis Streams worker runtime
"""
from __future__ import annotations

import asyncio
import os
import socket
from contextlib import suppress

from app.core.config import (
    APP_ROLE,
    ENABLE_BROWSER,
    MIGRATION_RUNNER_APP_ROLE,
    REDIS_URL,
    WORKER_ASYNC_CONSUMERS,
)
from app.core.logging import get_logger
from app.db.connection import close_db_runtime
from app.platform.apify_budget import (
    ApifyBudgetBlocked,
    ApifyExecutionClaimBlocked,
    ApifyProviderReplayBlocked,
    acquire_provider_execution_claim,
    apify_execution_context,
    finalize_provider_execution_claim,
    renew_provider_execution_claim,
)
from app.services.jobs.processor import process_background_job
from app.services.jobs.queue import RedisJobQueue
from app.services.scraping.playwright_scraper import _start_browser, _stop_browser
from app.workers.redis_worker_runtime import (
    RedisWorkerIdentity,
    build_redis_worker_identity,
    redis_worker_concurrency,
    redis_worker_db_preflight,
    redis_worker_heartbeat_loop,
    redis_worker_heartbeat_interval,
    stale_backlog_preflight,
    upsert_redis_worker_heartbeat,
)


logger = get_logger(__name__)


_ACKABLE_HANDLER_STATUSES = {"done", "partial_done", "prefilter_rejected", "cancelled"}
_PROVIDER_CLAIM_LEASE_SECONDS = max(
    120, min(3600, int(os.getenv("VKPI_PROVIDER_CLAIM_LEASE_SECONDS", "300")))
)
_PROVIDER_CLAIM_RENEW_SECONDS = max(
    15,
    min(
        _PROVIDER_CLAIM_LEASE_SECONDS // 3,
        int(os.getenv("VKPI_PROVIDER_CLAIM_RENEW_SECONDS", "60")),
    ),
)


def _consumer_name(slot: int, identity: RedisWorkerIdentity | None = None) -> str:
    prefix = (
        f"{identity.worker_name}-{identity.boot_nonce_sha256[:12]}"
        if identity is not None
        else f"redis-worker-{socket.gethostname()}-{os.getpid()}"
    )
    return f"{prefix}-slot-{slot}"


async def _provider_claim_renew_loop(
    *,
    task_id: str,
    fence_token: int,
    lease_owner: str,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=_PROVIDER_CLAIM_RENEW_SECONDS
            )
            return
        except asyncio.TimeoutError:
            pass
        renewed = await asyncio.to_thread(
            renew_provider_execution_claim,
            task_id,
            fence_token,
            lease_owner,
            lease_seconds=_PROVIDER_CLAIM_LEASE_SECONDS,
        )
        if not renewed:
            raise ApifyExecutionClaimBlocked(
                "redis worker lost its durable provider execution lease"
            )


async def _consumer_loop(
    queue: RedisJobQueue,
    slot: int,
    identity: RedisWorkerIdentity | None = None,
) -> None:
    consumer_name = _consumer_name(slot, identity)
    while True:
        raw_job = await queue.pop_job(consumer_name=consumer_name, timeout=5)
        if not raw_job:
            continue
        task_id = str(raw_job.get("task_id") or "").strip()
        fence = 0
        claim_stop = asyncio.Event()
        claim_renewal: asyncio.Task[None] | None = None
        handler_task: asyncio.Task[None] | None = None
        try:
            fence = await asyncio.to_thread(
                acquire_provider_execution_claim,
                task_id,
                consumer_name,
                job_type=str(raw_job.get("job_type") or ""),
                lease_seconds=_PROVIDER_CLAIM_LEASE_SECONDS,
            )
            with apify_execution_context(task_id, fence):
                claim_renewal = asyncio.create_task(
                    _provider_claim_renew_loop(
                        task_id=task_id,
                        fence_token=fence,
                        lease_owner=consumer_name,
                        stop_event=claim_stop,
                    )
                )
                handler_task = asyncio.create_task(
                    process_background_job(queue, raw_job)
                )
                done, _ = await asyncio.wait(
                    {handler_task, claim_renewal},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if claim_renewal in done:
                    # The renewer only exits normally after claim_stop.  While
                    # the handler is live, any exit means the durable lease was
                    # lost or its database became unverifiable.
                    claim_renewal.result()
                    raise ApifyExecutionClaimBlocked(
                        "provider execution lease renewal stopped unexpectedly"
                    )
                await handler_task
            claim_stop.set()
            if claim_renewal is not None:
                await claim_renewal
            current = await queue.get_status(task_id)
            status = str((current or {}).get("status") or "").strip().lower()
            if status in _ACKABLE_HANDLER_STATUSES:
                await asyncio.to_thread(
                    finalize_provider_execution_claim,
                    task_id,
                    fence,
                    "completed",
                )
                await queue.ack(raw_job)
            else:
                await asyncio.to_thread(
                    finalize_provider_execution_claim,
                    task_id,
                    fence,
                    "failed",
                )
                await queue.move_to_dead_letter(
                    raw_job,
                    f"handler returned without an ackable terminal status: {status or 'unknown'}",
                )
        except asyncio.CancelledError:
            raise
        except ApifyBudgetBlocked as exc:
            if fence:
                await asyncio.to_thread(
                    finalize_provider_execution_claim,
                    task_id,
                    fence,
                    "blocked",
                )
            await queue.move_to_dead_letter(
                raw_job,
                f"budget_blocked: {exc.code}: {exc.decision.reason}",
            )
        except ApifyExecutionClaimBlocked as exc:
            # Another live owner (or a lost renewal) is not a terminal job
            # outcome.  Leave the message pending and unacked; the real owner
            # can still finish/XACK it, or a later consumer can reclaim it only
            # after the durable lease expires.
            current = await queue.get_status(task_id)
            if str((current or {}).get("status") or "").lower() not in _ACKABLE_HANDLER_STATUSES:
                await queue.set_status(
                    task_id,
                    "retrying",
                    stage="provider_execution_live",
                    error_message=str(exc)[:300],
                )
            logger.warning(
                "redis job left pending behind live provider fence | task_id=%s consumer=%s",
                task_id,
                consumer_name,
            )
        except ApifyProviderReplayBlocked as exc:
            if fence:
                await asyncio.to_thread(
                    finalize_provider_execution_claim,
                    task_id,
                    fence,
                    "unknown",
                )
            await queue.move_to_dead_letter(
                raw_job,
                f"provider_execution_fenced: {getattr(exc, 'code', type(exc).__name__)}",
            )
        except Exception as exc:
            if fence:
                await asyncio.to_thread(
                    finalize_provider_execution_claim,
                    task_id,
                    fence,
                    "failed",
                )
            await queue.move_to_dead_letter(raw_job, f"{type(exc).__name__}: {exc}")
        finally:
            claim_stop.set()
            if handler_task is not None and not handler_task.done():
                handler_task.cancel()
                with suppress(asyncio.CancelledError):
                    await handler_task
            if claim_renewal is not None and not claim_renewal.done():
                claim_renewal.cancel()
                with suppress(asyncio.CancelledError):
                    await claim_renewal


async def _worker_loop() -> None:
    if APP_ROLE == MIGRATION_RUNNER_APP_ROLE:
        raise RuntimeError(
            "APP_ROLE='migration-runner' cannot start the Redis worker runtime"
        )
    if not REDIS_URL:
        raise RuntimeError("REDIS_URL is required to run the standalone worker")

    # Release deployment owns schema migrations and seeders.  The worker only
    # opens the existing runtime and proves its queue/heartbeat schema before
    # any heartbeat write or Redis claim.
    await asyncio.to_thread(redis_worker_db_preflight)
    identity = build_redis_worker_identity()
    consumer_count = redis_worker_concurrency(WORKER_ASYNC_CONSUMERS)
    # This gate is deliberately before queue construction/XREADGROUP.  It may
    # read the ledger, but can never claim or acknowledge the 16 historical
    # messages that currently require manual adjudication.
    backlog = await asyncio.to_thread(stale_backlog_preflight)
    queue = RedisJobQueue(REDIS_URL)
    consumer_names = [_consumer_name(idx + 1, identity) for idx in range(consumer_count)]
    readiness = await queue.worker_readiness(consumer_names)
    await asyncio.to_thread(
        upsert_redis_worker_heartbeat,
        identity,
        readiness,
        interval_seconds=redis_worker_heartbeat_interval(),
    )
    heartbeat_stop = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        redis_worker_heartbeat_loop(
            identity,
            heartbeat_stop,
            lambda: queue.worker_readiness(consumer_names),
        )
    )
    if ENABLE_BROWSER:
        await _start_browser()

    consumers = [
        asyncio.create_task(_consumer_loop(queue, idx + 1, identity))
        for idx in range(consumer_count)
    ]
    logger.info(
        "standalone redis worker started | async_consumers=%s worker=%s sha=%s stale_backlog=%s",
        len(consumers),
        identity.worker_name,
        identity.worker_git_sha[:8],
        backlog.get("stale_active_count"),
    )
    try:
        done, _ = await asyncio.wait(
            [heartbeat_task, *consumers],
            return_when=asyncio.FIRST_COMPLETED,
        )
        # Every runtime task is expected to be long-lived.  A readiness probe
        # failure is surfaced here (after it revokes redis_ready in Postgres),
        # causing systemd to observe a non-zero exit and restart the unit.
        for task in done:
            task.result()
        raise RuntimeError("redis worker runtime task exited unexpectedly")
    finally:
        heartbeat_stop.set()
        heartbeat_task.cancel()
        for task in consumers:
            task.cancel()
        with suppress(Exception):
            await asyncio.gather(*consumers, return_exceptions=True)
        with suppress(asyncio.CancelledError, Exception):
            await heartbeat_task
        await queue.close()
        if ENABLE_BROWSER:
            await _stop_browser()
        await close_db_runtime()


def main() -> None:
    asyncio.run(_worker_loop())


if __name__ == "__main__":
    main()
