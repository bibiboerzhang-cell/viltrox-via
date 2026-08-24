"""Pure budget-window projection shared by read and execution paths."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


_DAILY_WINDOW_SCOPES = frozenset({"dashboard:report_analysis"})


def budget_window_kind(scope: str) -> str:
    """Return the runtime window contract for one normalized budget scope.

    Scope prefixes, rather than mutable metadata, are the enforcement source of
    truth.  Metadata may describe the same contract for operators, but it must
    never silently change reset behavior.
    """

    key = str(scope or "").strip().lower()
    if not key:
        return "none"
    if key == "single_call" or key.startswith("single_call_"):
        return "per_call"
    if key.startswith("cron:") or key in _DAILY_WINDOW_SCOPES:
        return "daily"
    return "monthly"


def project_budget_window(
    row: dict[str, Any],
    *,
    now: datetime | None = None,
) -> tuple[dict[str, Any], bool, bool]:
    """Project a due daily/monthly reset without performing I/O.

    Returns ``(effective_row, roll_required, zero_spend)``.  A monthly scope
    without a prior reset anchor only receives its first anchor; its historical
    spend is intentionally preserved.
    """

    scope = str(row.get("scope") or "")
    window = budget_window_kind(scope)
    daily = window == "daily"
    # 功能 scope(audience_stats / vkpi_kol_content_fit / kol_recall / agent_* …)此前既非日窗也非月窗,
    # cap 变成终身额度:prod audience_stats $10 花到 $9.98 后受众年龄推断静默降级 rule_v0 数月。
    # 现按月滚;single_call* 是单次上限语义、不滚。
    monthly = window == "monthly"
    if not daily and not monthly:
        return row, False, False

    current_time = now or datetime.now(timezone.utc)
    reset_raw = str(row.get("reset_at") or "").strip()
    due = True
    if reset_raw:
        try:
            due = current_time >= datetime.fromisoformat(
                reset_raw.replace("Z", "+00:00")
            )
        except (TypeError, ValueError):
            due = True
    if not due:
        return row, False, False

    if daily:
        next_reset = (current_time + timedelta(days=1)).strftime(
            "%Y-%m-%dT00:00:00Z"
        )
    elif current_time.month == 12:
        next_reset = f"{current_time.year + 1:04d}-01-01T00:00:00Z"
    else:
        next_reset = (
            f"{current_time.year:04d}-{current_time.month + 1:02d}"
            "-01T00:00:00Z"
        )

    zero_spend = daily or bool(reset_raw)
    projected = dict(row)
    if zero_spend:
        projected["current_spend"] = 0
    projected["reset_at"] = next_reset
    return projected, True, zero_spend
