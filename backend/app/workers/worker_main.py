"""
workers/worker_main.py — dedicated Redis Streams worker runtime
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import sys
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import (
    APP_ROLE,
    ENABLE_BROWSER,
    MIGRATION_RUNNER_APP_ROLE,
    REDIS_URL,
    WORKER_ASYNC_CONSUMERS,
)
from app.core.logging import get_logger
from app.core.release_validation import release_validation_active
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
from app.services.jobs.queue import RedisJobQueue
from app.services.jobs.queue_common import parse_ts
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
from app.workers.contact_acquisition_worker import (
    periodic_cycle_loop as contact_acquisition_periodic_cycle_loop,
)
from app.workers.job_subprocess import (
    EXIT_BUDGET_BLOCKED,
    EXIT_EXECUTION_CLAIM_BLOCKED,
    EXIT_PROVIDER_REPLAY_BLOCKED,
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
_DEFAULT_HANDLER_TIMEOUT_SECONDS = max(
    1, int(os.getenv("VKPI_WORKER_HANDLER_TIMEOUT_SECONDS", "300"))
)
_HANDLER_TERMINATE_GRACE_SECONDS = max(
    0.1,
    min(10.0, float(os.getenv("VKPI_WORKER_HANDLER_TERMINATE_GRACE_SECONDS", "2"))),
)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_BACKEND_ROOT = _PROJECT_ROOT / "backend"


class WorkerHandlerDeadlineExceeded(TimeoutError):
    """The live handler was cancelled after exhausting its ledger deadline."""


class WorkerIsolatedBudgetBlocked(RuntimeError):
    """The isolated handler reported a typed pre-provider budget denial."""


class WorkerHandlerProcessError(RuntimeError):
    """The isolated handler exited without an acknowledged typed outcome."""


class WorkerHandlerTerminationError(RuntimeError):
    """The worker could not prove that its isolated process group stopped."""


def _handler_deadline_seconds(
    dispatch: dict[str, object],
    *,
    now: datetime | None = None,
) -> float:
    """Use the durable ledger timeout as the execution deadline.

    The queue timeout sweeper remains a secondary crash-recovery check.  The
    worker itself owns cancellation while it is alive, so a timed-out handler
    cannot keep a consumer slot pinned indefinitely.
    """

    started_at = parse_ts(dispatch.get("started_at"))
    if started_at is None:
        raise WorkerHandlerProcessError(
            "authorized dispatch requires a parseable ledger started_at"
        )
    try:
        configured = float(dispatch.get("timeout_seconds") or _DEFAULT_HANDLER_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        configured = float(_DEFAULT_HANDLER_TIMEOUT_SECONDS)
    timeout_seconds = max(0.01, min(86_400.0, configured))
    observed_at = now or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    elapsed = max(0.0, (observed_at - started_at).total_seconds())
    return max(0.01, timeout_seconds - elapsed)


def _handler_subprocess_environment() -> dict[str, str]:
    env = dict(os.environ)
    current_pythonpath = str(env.get("PYTHONPATH") or "").strip()
    env["PYTHONPATH"] = (
        f"{_BACKEND_ROOT}{os.pathsep}{current_pythonpath}"
        if current_pythonpath
        else str(_BACKEND_ROOT)
    )
    env.update({
        "VKPI_HANDLER_SUBPROCESS": "1",
        "POSTGRES_POOL_MIN_SIZE": "1",
        "POSTGRES_POOL_MAX_SIZE": "4",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return env


async def _signal_handler_process(
    process: asyncio.subprocess.Process,
    sig: signal.Signals,
) -> None:
    try:
        if os.name == "posix":
            # The session leader may already be reaped while a descendant that
            # ignored SIGTERM still owns this process group.  Always address
            # the saved pgid, even after the leader has exited.
            os.killpg(process.pid, sig)
        elif process.returncode is None and sig == signal.SIGTERM:  # pragma: no cover
            process.terminate()
        elif process.returncode is None:  # pragma: no cover
            process.kill()
    except ProcessLookupError:
        return


def _handler_process_group_exists(pgid: int) -> bool:
    if os.name != "posix":  # pragma: no cover - production is Linux
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _wait_handler_process_group_absent(pgid: int, timeout: float) -> bool:
    deadline = asyncio.get_running_loop().time() + max(0.0, timeout)
    while _handler_process_group_exists(pgid):
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(0.05, remaining))
    return True


async def _terminate_handler_process(process: asyncio.subprocess.Process) -> None:
    """Terminate and reap the private handler process group before returning."""

    pgid = process.pid
    await _signal_handler_process(process, signal.SIGTERM)
    if process.returncode is None:
        try:
            await asyncio.wait_for(
                process.wait(), timeout=_HANDLER_TERMINATE_GRACE_SECONDS
            )
        except asyncio.TimeoutError:
            pass
    group_gone = (
        await _wait_handler_process_group_absent(
            pgid, _HANDLER_TERMINATE_GRACE_SECONDS
        )
        if os.name == "posix"
        else process.returncode is not None
    )
    if not group_gone:
        await _signal_handler_process(process, signal.SIGKILL)
    if process.returncode is None:
        await process.wait()
    if not await _wait_handler_process_group_absent(
        pgid, _HANDLER_TERMINATE_GRACE_SECONDS
    ):
        raise RuntimeError(
            f"isolated handler process group pgid={pgid} could not be reaped"
        )


async def _communicate_handler_process(
    process: asyncio.subprocess.Process,
    request: bytes,
) -> tuple[bytes | None, bytes | None]:
    """Keep pipe readers alive while cancellation kills and reaps the child."""

    communication = asyncio.create_task(process.communicate(request))
    try:
        return await asyncio.shield(communication)
    except BaseException:
        await _terminate_handler_process(process)
        with suppress(asyncio.CancelledError, Exception):
            await asyncio.shield(communication)
        raise


async def _run_handler(
    _queue: RedisJobQueue,
    raw_job: dict[str, object],
    fence_token: int,
) -> None:
    """Execute one handler in a killable process group, never a local thread."""

    request = json.dumps(
        {"raw_job": raw_job, "fence_token": int(fence_token)},
        ensure_ascii=False,
    ).encode("utf-8")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.workers.job_subprocess",
        cwd=str(_PROJECT_ROOT),
        env=_handler_subprocess_environment(),
        stdin=asyncio.subprocess.PIPE,
        start_new_session=os.name == "posix",
    )
    _stdout, stderr = await _communicate_handler_process(process, request)
    error = (stderr or b"").decode("utf-8", errors="replace")[-1000:].strip()
    if process.returncode == 0:
        return
    if process.returncode == EXIT_BUDGET_BLOCKED:
        raise WorkerIsolatedBudgetBlocked(error or "isolated provider budget blocked")
    if process.returncode == EXIT_PROVIDER_REPLAY_BLOCKED:
        raise ApifyProviderReplayBlocked(error or "isolated provider replay blocked")
    if process.returncode == EXIT_EXECUTION_CLAIM_BLOCKED:
        raise ApifyExecutionClaimBlocked(error or "isolated execution claim blocked")
    raise WorkerHandlerProcessError(
        f"isolated handler exited code={process.returncode}: {error or 'no stderr'}"
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


async def _await_handler_outcome(
    handler_task: asyncio.Task[None],
    claim_renewal: asyncio.Task[None],
    handler_deadline: asyncio.Task[None],
    claim_stop: asyncio.Event,
    *,
    task_id: str,
    timeout_seconds: float,
) -> None:
    done, _ = await asyncio.wait(
        {handler_task, claim_renewal, handler_deadline},
        return_when=asyncio.FIRST_COMPLETED,
    )
    if handler_task in done:
        await handler_task
        return
    if claim_renewal in done:
        try:
            claim_renewal.result()
        except BaseException:
            await _cancel_live_handler(handler_task, claim_stop, task_id=task_id)
            raise
        await _cancel_live_handler(handler_task, claim_stop, task_id=task_id)
        raise ApifyExecutionClaimBlocked(
            "provider execution lease renewal stopped unexpectedly"
        )
    await _cancel_live_handler(handler_task, claim_stop, task_id=task_id)
    raise WorkerHandlerDeadlineExceeded(
        f"handler exceeded remaining_timeout_seconds={timeout_seconds:g}; "
        "provider outcome is unknown and automatic replay is blocked"
    )


async def _cancel_live_handler(
    handler_task: asyncio.Task[None],
    claim_stop: asyncio.Event,
    *,
    task_id: str,
) -> None:
    """Do not release a claim or mutate queue state until the child is gone."""

    claim_stop.set()
    handler_task.cancel()
    try:
        await handler_task
    except asyncio.CancelledError:
        return
    except Exception:
        logger.warning(
            "redis handler raised during forced cancellation | task_id=%s",
            task_id,
            exc_info=True,
        )
        raise WorkerHandlerTerminationError(
            f"isolated handler termination could not be proven for task_id={task_id}"
        )


async def _consumer_loop(
    queue: RedisJobQueue,
    slot: int,
    identity: RedisWorkerIdentity | None = None,
) -> None:
    consumer_name = _consumer_name(slot, identity)
    while True:
        # Keep the process/Redis heartbeat observable during release proof, but
        # never issue XREADGROUP/XCLAIM until the root-owned activation fence
        # has been removed.
        if release_validation_active():
            await asyncio.sleep(1)
            continue
        raw_job = await queue.pop_job(consumer_name=consumer_name, timeout=5)
        if not raw_job:
            continue
        # The marker can appear while XREADGROUP is blocked. Leave the message
        # pending and make no provider claim; it can be reclaimed only after
        # activation removes the release fence.
        if release_validation_active():
            continue
        task_id = str(raw_job.get("task_id") or "").strip()
        fence = 0
        claim_stop = asyncio.Event()
        claim_renewal: asyncio.Task[None] | None = None
        handler_task: asyncio.Task[None] | None = None
        handler_deadline: asyncio.Task[None] | None = None
        handler_timeout_seconds = float(_DEFAULT_HANDLER_TIMEOUT_SECONDS)
        try:
            fence = await asyncio.to_thread(
                acquire_provider_execution_claim,
                task_id,
                consumer_name,
                job_type=str(raw_job.get("job_type") or ""),
                lease_seconds=_PROVIDER_CLAIM_LEASE_SECONDS,
            )
            dispatch = await queue.authorize_provider_dispatch(
                task_id,
                str(raw_job.get("_stream_id") or ""),
            )
            if not dispatch.get("authorized"):
                status = str(dispatch.get("status") or "").strip().lower()
                await asyncio.to_thread(
                    finalize_provider_execution_claim,
                    task_id,
                    fence,
                    "completed" if status in _ACKABLE_HANDLER_STATUSES else "failed",
                )
                await queue.ack(raw_job)
                logger.warning(
                    "redis handler dispatch blocked by ledger state | task_id=%s status=%s",
                    task_id,
                    status or "missing",
                )
                continue
            handler_timeout_seconds = _handler_deadline_seconds(dispatch)
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
                    _run_handler(queue, raw_job, fence)
                )
                handler_deadline = asyncio.create_task(
                    asyncio.sleep(handler_timeout_seconds)
                )
                await _await_handler_outcome(
                    handler_task,
                    claim_renewal,
                    handler_deadline,
                    claim_stop,
                    task_id=task_id,
                    timeout_seconds=handler_timeout_seconds,
                )
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
        except WorkerIsolatedBudgetBlocked as exc:
            if fence:
                await asyncio.to_thread(
                    finalize_provider_execution_claim,
                    task_id,
                    fence,
                    "blocked",
                )
            await queue.move_to_dead_letter(
                raw_job,
                f"budget_blocked: {str(exc)[:500]}",
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
        except WorkerHandlerDeadlineExceeded as exc:
            # Cancellation can race a remote provider call.  Preserve an
            # unknown provider outcome and terminalize to DLQ; automatic retry
            # could duplicate a paid/external side effect.
            if fence:
                await asyncio.to_thread(
                    finalize_provider_execution_claim,
                    task_id,
                    fence,
                    "unknown",
                )
            await queue.move_to_dead_letter(
                raw_job,
                f"handler_timeout_no_retry: {exc}",
            )
            logger.error(
                "redis handler deadline exceeded; moved to DLQ without retry | "
                "task_id=%s timeout_seconds=%s",
                task_id,
                handler_timeout_seconds,
            )
        except WorkerHandlerTerminationError:
            # Fail closed: do not release/finalize the claim or mutate queue
            # state unless the old process group is proven absent.
            logger.critical(
                "redis handler process group termination unproven; "
                "consumer is stopping before ledger mutation | task_id=%s",
                task_id,
                exc_info=True,
            )
            raise
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
            if handler_deadline is not None and not handler_deadline.done():
                handler_deadline.cancel()
                with suppress(asyncio.CancelledError):
                    await handler_deadline


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
    contact_acquisition_stop = asyncio.Event()
    contact_acquisition_task = asyncio.create_task(
        contact_acquisition_periodic_cycle_loop(contact_acquisition_stop)
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
            [heartbeat_task, contact_acquisition_task, *consumers],
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
        contact_acquisition_stop.set()
        heartbeat_task.cancel()
        contact_acquisition_task.cancel()
        for task in consumers:
            task.cancel()
        with suppress(Exception):
            await asyncio.gather(*consumers, return_exceptions=True)
        with suppress(asyncio.CancelledError, Exception):
            await heartbeat_task
        with suppress(asyncio.CancelledError, Exception):
            await contact_acquisition_task
        await queue.close()
        if ENABLE_BROWSER:
            await _stop_browser()
        await close_db_runtime()


def main() -> None:
    asyncio.run(_worker_loop())


if __name__ == "__main__":
    main()
