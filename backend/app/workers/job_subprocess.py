"""One durable job handler executed inside a disposable process boundary.

The Redis consumer owns the durable provider claim and renews it while this
child is alive.  The child receives only the already-claimed raw job and fence
token over stdin.  A worker deadline can therefore terminate the complete
process group, including synchronous ``asyncio.to_thread`` work and descendants,
before the parent releases or terminalizes the claim.
"""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from app.core.config import REDIS_URL
from app.db.connection import close_db_runtime
from app.platform.apify_budget import (
    ApifyBudgetBlocked,
    ApifyExecutionClaimBlocked,
    ApifyProviderReplayBlocked,
    apify_execution_context,
)
from app.services.jobs.processor import process_background_job
from app.services.jobs.queue import RedisJobQueue


EXIT_BUDGET_BLOCKED = 41
EXIT_PROVIDER_REPLAY_BLOCKED = 42
EXIT_EXECUTION_CLAIM_BLOCKED = 43


def _write_stderr(message: object) -> None:
    sys.stderr.write(f"{str(message)[:1000]}\n")
    sys.stderr.flush()


def _request() -> tuple[dict[str, Any], int]:
    document = json.loads(sys.stdin.buffer.read())
    if not isinstance(document, dict) or not isinstance(document.get("raw_job"), dict):
        raise ValueError("isolated handler requires a raw_job object")
    fence_token = int(document.get("fence_token") or 0)
    if fence_token <= 0:
        raise ValueError("isolated handler requires a positive fence_token")
    return dict(document["raw_job"]), fence_token


async def _run(raw_job: dict[str, Any], fence_token: int) -> None:
    task_id = str(raw_job.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("isolated handler requires task_id")
    queue = RedisJobQueue(REDIS_URL)
    try:
        with apify_execution_context(task_id, fence_token):
            await process_background_job(queue, raw_job)
    finally:
        await _best_effort_cleanup(queue)


async def _best_effort_cleanup(queue: RedisJobQueue) -> None:
    """Never let teardown replace the handler's durable outcome."""

    for label, cleanup in (
        ("queue", queue.close),
        ("database", close_db_runtime),
    ):
        try:
            await cleanup()
        except Exception as exc:
            _write_stderr(
                f"isolated handler {label} cleanup failed: "
                f"{type(exc).__name__}: {str(exc)[:500]}"
            )


def main() -> int:
    try:
        raw_job, fence_token = _request()
        asyncio.run(_run(raw_job, fence_token))
        return 0
    except ApifyBudgetBlocked as exc:
        _write_stderr(exc)
        return EXIT_BUDGET_BLOCKED
    except ApifyProviderReplayBlocked as exc:
        _write_stderr(exc)
        return EXIT_PROVIDER_REPLAY_BLOCKED
    except ApifyExecutionClaimBlocked as exc:
        _write_stderr(exc)
        return EXIT_EXECUTION_CLAIM_BLOCKED
    except BaseException as exc:
        _write_stderr(f"{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
