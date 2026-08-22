"""Pure budget-window projection shared by read and execution paths."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


_DAILY_WINDOW_SCOPES = ("dashboard:report_analysis",)


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
    daily = scope.startswith("cron:") or scope in _DAILY_WINDOW_SCOPES
    monthly = scope == "monthly_total" or scope.startswith("provider:")
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
