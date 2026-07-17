"""Market-listening scheduler registration (extracted to keep jobs.py <1000).

Registers the daily market-listening collect job (07:40 China): dual env gates
VKPI_FORUM_COLLECT_ENABLED / VKPI_X_COLLECT_ENABLED plus the config-gate
scheduler_tasks.vkpi_market_listening (absent row -> default off, no-op run).
Lands vkpi_market_sources / vkpi_market_mentions idempotently and feeds the
"recent market hashtags" module. Enable details live in the job docstring.
"""
from __future__ import annotations

from typing import Any


def register_market_listening_job(scheduler: Any, china_tz: Any) -> None:
    from apscheduler.triggers.cron import CronTrigger

    from app.services.scheduler.jobs_tasks_intel import (
        job_vkpi_market_listening_daily,
    )

    scheduler.add_job(
        job_vkpi_market_listening_daily,
        trigger=CronTrigger(hour=7, minute=40, timezone=china_tz),
        id="vkpi_market_listening_daily",
        name="Market listening daily collect (Reddit free JSON + X Apify capped, default-off)",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
