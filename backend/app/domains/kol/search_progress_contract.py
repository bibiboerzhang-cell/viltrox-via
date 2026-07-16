"""Truthful completion semantics for progressive KOL search sessions.

``complete`` and ``required_tasks_complete`` are retained as compatibility
aliases for "every requested task has reached a terminal state".  They are not
evidence that the optional full-analysis pipeline ran.  The strict
``full_analysis_complete`` flag requires every item to have durable, successful
video, comments, and audience stages; ``not_requested`` can therefore never be
reported as a full analysis.
"""
from __future__ import annotations

from typing import Any, Mapping


FULL_ANALYSIS_ROLES = ("video", "comments", "audience")


def _count(stage: Mapping[str, Any], key: str) -> int:
    try:
        return max(0, int(stage.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def completion_contract(
    *,
    base_count: int,
    total: int,
    terminal_count: int,
    ready_count: int,
    profile_failed: int = 0,
    active_tasks: int = 0,
    stage_progress: Mapping[str, Mapping[str, Any]] | None = None,
    requested_tasks_terminal: bool | None = None,
) -> dict[str, bool]:
    """Return backward-compatible and strict progressive-completion flags.

    ``requested_tasks_terminal`` may be supplied by an orchestrator that knows
    more work will be registered after the current profile batch.  Otherwise it
    is derived from terminal item counts and active tasks.
    """

    safe_total = max(0, int(total or 0))
    safe_base = max(0, int(base_count or 0))
    safe_terminal = max(0, int(terminal_count or 0))
    safe_ready = max(0, int(ready_count or 0))
    safe_profile_failed = max(0, int(profile_failed or 0))
    safe_active = max(0, int(active_tasks or 0))

    base_complete = safe_total > 0 and safe_base >= safe_total
    terminal = (
        bool(requested_tasks_terminal)
        if requested_tasks_terminal is not None
        else safe_total > 0 and safe_terminal >= safe_total and safe_active == 0
    )

    stages = stage_progress if isinstance(stage_progress, Mapping) else None
    full_analysis_complete = bool(terminal and stages is not None and safe_total > 0)
    if full_analysis_complete:
        for role in FULL_ANALYSIS_ROLES:
            raw_stage = stages.get(role)
            if not isinstance(raw_stage, Mapping):
                full_analysis_complete = False
                break
            if (
                _count(raw_stage, "ready") < safe_total
                or _count(raw_stage, "active") > 0
                or _count(raw_stage, "failed") > 0
                or _count(raw_stage, "not_requested") > 0
            ):
                full_analysis_complete = False
                break

    decision_eligible = bool(
        full_analysis_complete
        and safe_profile_failed == 0
        and safe_ready >= safe_total
    )
    return {
        "base_complete": base_complete,
        "requested_tasks_terminal": terminal,
        "full_analysis_complete": full_analysis_complete,
        "decision_eligible": decision_eligible,
        # Compatibility aliases.  They intentionally retain terminal—not full
        # analysis—semantics for old clients.
        "required_tasks_complete": terminal,
        "complete": terminal,
    }


__all__ = ["FULL_ANALYSIS_ROLES", "completion_contract"]
