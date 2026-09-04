"""Registration helper for the bounded Smart Search inventory refresh."""
from __future__ import annotations

from typing import Any

from apscheduler.triggers.cron import CronTrigger

from app.services.scheduler.jobs import (
    US_EASTERN_TZ,
    job_kol_profile_incremental_refresh,
)


def register_kol_profile_incremental_refresh(scheduler: Any) -> None:
    """Register the queue-only, config-gated daily refresh callback."""

    scheduler.add_job(
        job_kol_profile_incremental_refresh,
        trigger=CronTrigger(hour=3, minute=20, timezone=US_EASTERN_TZ),
        id="kol_profile_incremental_refresh",
        name="Daily bounded Smart Search one-post inventory refresh",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )


__all__ = ["register_kol_profile_incremental_refresh"]
