"""Service facade for P6.77 read-only weekly action plan v0."""
from __future__ import annotations

from app.domains.intelligence import today_signals_use_case as today_new_signals_v0
from app.domains.intelligence.weekly_plan import PLAN_VERSION, build_weekly_action_plan_report
from app.domains.launch import acceptance_use_case as new_launch_acceptance_v0


def build_weekly_action_plan_v0(
    *,
    sku: str = "",
    top_n: int = 12,
    lookback_hours: int = 24,
) -> dict:
    bounded_top = max(1, min(50, int(top_n or 12)))
    acceptance = new_launch_acceptance_v0.build_new_launch_acceptance_v0(
        sku=sku,
        kol_limit=200,
        top_n=bounded_top,
        lookback_days=max(1, int((lookback_hours + 23) / 24)),
    )
    signals = today_new_signals_v0.build_today_new_signals_v0(
        lookback_hours=max(1, min(168, int(lookback_hours or 24))),
        limit=100,
    )
    return build_weekly_action_plan_report(
        acceptance=acceptance,
        signals=signals,
        sku=sku,
        top_n=bounded_top,
        lookback_hours=lookback_hours,
    )


__all__ = [
    "PLAN_VERSION",
    "build_weekly_action_plan_v0",
    "new_launch_acceptance_v0",
    "today_new_signals_v0",
]
