"""Write one truthful scheduler registry result without premature success."""
from __future__ import annotations

from app.core.logging import get_logger

logger = get_logger(__name__)


def record_scheduler_run(task_key: str, *, ok: bool, error: str = "", status: str = "") -> None:
    try:
        from app.domains.ops import scheduler_registry
        from .fleet_guard_claim import mark_scheduled_fire_result
        from .jobs_tasks_intel import _RUN_RECORD_SLOT, _note_run_record_slot
        from app.services.scheduler_result_contract import normalize_scheduler_record

        outcome = normalize_scheduler_record(ok=ok, status=status, error=error)
        # Defer the metadata write until the wrapper sees the final result.
        # A callback recording ok=True and then failing never advances success.
        slot = _RUN_RECORD_SLOT.get()
        if slot is not None and slot.get("task_key") == task_key and not slot.get("flushing"):
            previous = slot.get("pending_record")
            severity = {"completed": 0, "blocked": 1, "failed": 2}
            if previous is None or severity[outcome.status] > severity[previous[0].status]:
                slot["pending_record"] = (outcome, status)
            mark_scheduled_fire_result(outcome)
            return
        mark_scheduled_fire_result(outcome)
        extra = {"status": outcome.registry_status} if status else {}
        scheduler_registry.record_run(task_key, ok=outcome.ok, error=outcome.error, **extra)
        _note_run_record_slot(task_key, "recorded")
    except Exception:
        logger.debug("scheduler.record_run_helper_failed", extra={"task": task_key}, exc_info=True)
