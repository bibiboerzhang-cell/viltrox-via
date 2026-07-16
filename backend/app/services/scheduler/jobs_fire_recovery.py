"""Bounded recovery job for scheduler fire claims left by dead processes."""

from __future__ import annotations

import os
from typing import Any

from app.services.scheduler.fleet_guard import (
    recover_stale_scheduled_fires,
    scheduler_instance_id,
)


def scheduler_fire_recovery_interval_seconds() -> int:
    raw = str(
        os.environ.get("VKPI_SCHEDULER_FIRE_RECOVERY_INTERVAL_SECONDS") or "60"
    ).strip()
    try:
        seconds = int(raw)
    except ValueError as exc:
        raise RuntimeError(
            "VKPI_SCHEDULER_FIRE_RECOVERY_INTERVAL_SECONDS must be an integer"
        ) from exc
    if seconds < 30 or seconds > 3600:
        raise RuntimeError(
            "VKPI_SCHEDULER_FIRE_RECOVERY_INTERVAL_SECONDS must be between 30 and 3600"
        )
    return seconds


def job_scheduler_fire_stale_recovery() -> dict[str, Any]:
    """Terminalize stale claims without replaying their original side effects."""

    recovered = recover_stale_scheduled_fires(scheduler_instance_id())
    return {
        "status": "ok",
        "recovered_total": len(recovered),
        "automatic_replay": False,
        "claim_ids": [item.claim_id for item in recovered],
    }
