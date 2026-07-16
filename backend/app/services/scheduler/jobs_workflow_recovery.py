"""Bounded migration-265 workflow crash-recovery scheduler callback."""
from __future__ import annotations

import asyncio
import os

from app.core.logging import get_logger


logger = get_logger(__name__)


def _explicit_boolean_env(name: str) -> bool:
    raw = str(os.environ.get(name) or "0").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"", "0", "false", "no", "off"}:
        return False
    raise RuntimeError(
        f"{name} must be one of 0/1, false/true, no/yes, or off/on"
    )


def workflow_auto_recovery_enabled() -> bool:
    """Return whether automatic replay of failed workflows is explicitly enabled.

    Workflow state is lease-fenced, but several callbacks still call external
    sinks that do not provide exactly-once semantics.  Keep scheduler-driven
    replay opt-in until those sinks have durable idempotency and failed runs
    have a capped retry/dead-letter policy.
    """

    return _explicit_boolean_env("VKPI_WORKFLOW_AUTO_RECOVERY_ENABLED")


def workflow_scheduled_execution_enabled() -> bool:
    """Gate workflows that may resume failed/paused runs without a DLQ cap."""

    return _explicit_boolean_env("VKPI_WORKFLOW_SCHEDULED_EXECUTION_ENABLED")


async def job_vkpi_workflow_recovery() -> None:
    """Recover expired/failed/paused durable runs under fresh fences."""

    try:
        from app.domains.platform import workflow_recovery

        result = await asyncio.to_thread(
            workflow_recovery.sweep_recoverable_runs,
            None,
            limit=20,
            minimum_age_seconds=60,
        )
        logger.info(
            "scheduler.vkpi_workflow_recovery",
            extra={
                "scanned": result.get("scanned"),
                "completed": result.get("completed"),
                "failed": result.get("failed"),
                "unsupported": result.get("unsupported"),
            },
        )
    except Exception:
        logger.exception("scheduler.vkpi_workflow_recovery_failed")
