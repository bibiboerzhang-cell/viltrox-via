"""
workers/worker_main.py — dedicated Redis Streams worker runtime
"""
from __future__ import annotations

import asyncio
import os
import socket
from contextlib import suppress

from app.core.config import ENABLE_BROWSER, REDIS_URL, WORKER_ASYNC_CONSUMERS
from app.core.logging import get_logger
from app.db.connection import close_db_runtime, init_db_runtime
from app.services.jobs.processor import process_background_job
from app.services.jobs.queue import RedisJobQueue
from app.services.scraping.playwright_scraper import _start_browser, _stop_browser


logger = get_logger(__name__)


def _consumer_name(slot: int) -> str:
    return f"{socket.gethostname()}-{os.getpid()}-{slot}"


async def _consumer_loop(queue: RedisJobQueue, slot: int) -> None:
    consumer_name = _consumer_name(slot)
    while True:
        raw_job = await queue.pop_job(consumer_name=consumer_name, timeout=5)
        if not raw_job:
            continue
        try:
            await process_background_job(queue, raw_job)
            await queue.ack(raw_job)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await queue.move_to_dead_letter(raw_job, f"{type(exc).__name__}: {exc}")


async def _worker_loop() -> None:
    if not REDIS_URL:
        raise RuntimeError("REDIS_URL is required to run the standalone worker")

    await init_db_runtime()
    queue = RedisJobQueue(REDIS_URL)
    if ENABLE_BROWSER:
        await _start_browser()

    consumers = [asyncio.create_task(_consumer_loop(queue, idx + 1)) for idx in range(max(1, WORKER_ASYNC_CONSUMERS))]
    logger.info("standalone worker started | async_consumers=%s", len(consumers))
    try:
        await asyncio.gather(*consumers)
    finally:
        for task in consumers:
            task.cancel()
        with suppress(Exception):
            await asyncio.gather(*consumers, return_exceptions=True)
        await queue.close()
        if ENABLE_BROWSER:
            await _stop_browser()
        await close_db_runtime()


def main() -> None:
    asyncio.run(_worker_loop())


if __name__ == "__main__":
    main()
