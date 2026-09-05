"""Small, provider-free outcome contract shared by scheduler bookkeeping.

Legacy callbacks may return None (or an unstructured value) on success.  A
structured outcome is authoritative: accepting downstream work is not proof
that it completed.  Existing ledger schemas have no queued terminal state, so
pending work is stored as blocked:awaiting_completion with its actual status
in the diagnostic.  This never retries or cancels the downstream work.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


_COMPLETED = frozenset({"ok", "success", "succeeded", "completed", "done", "empty", "noop", "no_op", "ready"})
_PENDING = frozenset({"queued", "enqueued", "already_queued", "pending", "accepted", "submitted", "submitting", "in_progress", "running", "triggered", "dispatched"})
_BLOCKED = frozenset({"blocked", "disabled", "skipped", "not_started", "not_ready", "paused", "budget_exhausted", "release_validation_fenced"})


@dataclass(frozen=True)
class SchedulerOutcome:
    status: str
    error: str = ""
    reason_key: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "completed"

    @property
    def registry_status(self) -> str:
        return "ok" if self.ok else self.status

    @property
    def fire_status(self) -> str:
        return f"blocked:{self.reason_key}" if self.status == "blocked" else self.status


def normalize_scheduler_result(result: Any) -> SchedulerOutcome:
    """Preserve legacy success, but never promote explicit non-success results.

Counters are not inferred globally: a recovery task's failed count may refer
to failures it successfully terminalized.  Task adapters must expose their
own batch/dispatch semantics as a status instead.
    """
    if result is False:
        return SchedulerOutcome("failed", "callback_returned_false")
    if not isinstance(result, Mapping):
        return SchedulerOutcome("completed")
    status = str(result.get("status") or "").strip().lower()
    detail = str(result.get("error") or result.get("reason") or result.get("error_code") or "").strip()
    if status in _PENDING:
        return SchedulerOutcome("blocked", f"status={status}; awaiting_downstream_completion"[:500], "awaiting_completion")
    if status in _BLOCKED or status.startswith("blocked:"):
        reason = status.split(":", 1)[1] if status.startswith("blocked:") else status
        if status == "blocked" and detail.endswith("; awaiting_downstream_completion"):
            reason = "awaiting_completion"
        return SchedulerOutcome("blocked", (detail or f"status={status}")[:500], reason)
    if result.get("ok") is False:
        return SchedulerOutcome("failed", (detail or f"status={status or 'failed'}; ok=false")[:500])
    if not status or status in _COMPLETED:
        return SchedulerOutcome("completed")
    # Unknown structured states also fail closed instead of inventing success.
    return SchedulerOutcome("failed", (detail or f"status={status}")[:500])


def normalize_scheduler_record(*, ok: bool, status: str = "", error: str = "") -> SchedulerOutcome:
    """Keep explicit record_run callers consistent when ok and status disagree."""
    return normalize_scheduler_result({"ok": ok, "status": status or ("ok" if ok else "failed"), "error": error})


def scheduler_dispatch_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Adapt queue-only callback counters without changing the domain payload.

The scheduler's result exposes enqueue_status separately from completion;
the domain enqueue service retains its existing API contract.
    """
    adapted = dict(result)
    status = str(result.get("status") or "").strip().lower()
    if status not in _COMPLETED and status:
        return result if isinstance(result, dict) else adapted
    if int(result.get("failed") or 0) > 0:
        adapted.update(status="partial", enqueue_status=status or "ok")
    elif any(int(result.get(key) or 0) > 0 for key in ("queued", "already_queued", "enqueued_count", "channels_enqueued", "industry_accounts_enqueued")):
        adapted.update(status="queued", enqueue_status=status or "ok")
    return adapted
