"""Fail-closed scheduler registration policy for bounded local canaries."""

from __future__ import annotations

import os
import re
from typing import Any

_TASK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")


def scheduler_task_allowlist() -> frozenset[str] | None:
    """Return a strict task allowlist, or ``None`` for the normal registry."""

    raw = os.environ.get("VKPI_SCHEDULER_TASK_ALLOWLIST")
    if raw is None:
        return None
    task_ids = frozenset(part.strip() for part in raw.split(",") if part.strip())
    if not task_ids:
        raise RuntimeError(
            "VKPI_SCHEDULER_TASK_ALLOWLIST must contain at least one task id"
        )
    invalid = sorted(
        task_id for task_id in task_ids if not _TASK_ID_RE.fullmatch(task_id)
    )
    if invalid:
        raise RuntimeError(
            "VKPI_SCHEDULER_TASK_ALLOWLIST contains invalid task ids: "
            + ", ".join(invalid)
        )
    return task_ids


def enforce_scheduler_task_allowlist(scheduler: Any) -> dict[str, Any] | None:
    """Verify every configured task survived registration filtering."""

    allowlist = scheduler_task_allowlist()
    if allowlist is None:
        return None
    registered = {job.id for job in scheduler.get_jobs()}
    missing = sorted(allowlist - registered)
    if missing:
        raise RuntimeError(
            "VKPI_SCHEDULER_TASK_ALLOWLIST references unavailable tasks: "
            + ", ".join(missing)
        )
    return {
        "registered_task_ids": sorted(registered),
        "filtered_count": len(getattr(scheduler, "_vkpi_filtered_task_ids", [])),
    }
