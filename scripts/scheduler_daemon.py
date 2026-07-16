"""本地常驻 scheduler daemon —— 让大脑自己跑(直接起 APScheduler 引擎,keep-alive)。

由 launchd(com.viltrox.vkpi-scheduler)拉起;崩溃 launchd 自动重启。
"""
from stdout_utils import out as stdout_out
import asyncio
import os

from app.db.connection import db_connection_sync_scope
from app.services.scheduler import jobs


RUN_REQUEST_POLL_SECONDS = max(
    0.5,
    float(os.environ.get("VKPI_SCHEDULER_RUN_REQUEST_POLL_SECONDS", "2") or 2),
)
RUN_REQUEST_BATCH_LIMIT = max(
    1,
    min(50, int(os.environ.get("VKPI_SCHEDULER_RUN_REQUEST_BATCH_LIMIT", "10") or 10)),
)


def _dispatch_run_requests() -> dict:
    # The daemon runs this function in a reusable executor thread.  A bounded
    # scope returns the PostgreSQL lease after every poll and lets the pool
    # replace a broken connection instead of pinning it in thread-local state.
    with db_connection_sync_scope():
        return jobs.dispatch_queued_run_requests(limit=RUN_REQUEST_BATCH_LIMIT)


async def main() -> None:
    await jobs.start_scheduler()
    try:
        st = jobs.get_scheduler_status()
        n = len(st.get("jobs", []) if isinstance(st, dict) else [])
        stdout_out(f"SCHEDULER_STARTED jobs={n}", flush=True)
        last_request_status = ""
        while True:
            result = await asyncio.to_thread(_dispatch_run_requests)
            status = str(result.get("status") or "unknown")
            dispatched = int(result.get("dispatched") or 0)
            errors = int(result.get("errors") or 0)
            if status != last_request_status or dispatched or errors:
                # Deliberately log counts/state only: no task payload, provider
                # input, credential, or database exception text crosses stdout.
                stdout_out(
                    "SCHEDULER_RUN_REQUESTS "
                    f"status={status} dispatched={dispatched} errors={errors}",
                    flush=True,
                )
            last_request_status = status
            await asyncio.sleep(RUN_REQUEST_POLL_SECONDS)
    finally:
        await jobs.stop_scheduler()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        stdout_out("SCHEDULER_STOPPED signal=keyboard_interrupt", flush=True)
