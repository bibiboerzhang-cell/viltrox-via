"""Sync domain facade."""

from app.domains.sync.sentinel import (
    SENTINEL_VERSION,
    build_sync_sentinel_report,
    signal,
    signals_from_budgets,
    signals_from_open_alerts,
    signals_from_overview,
    signals_from_p6_79,
    sort_signals,
)
from app.domains.sync.sentinel_use_case import build_sync_sentinel_agent_v0
from app.domains.sync.guard import (
    SYNC_FAIL_FAST_EXIT_CODE,
    SYNC_GUARD_BLOCKED_EXIT_CODE,
    SyncFailFast,
    SyncGuardBlocked,
    ack_daily_sync_guard,
    check_daily_sync_guard,
    finish_sync_run,
    record_daily_sync_summary,
    record_sync_interrupt,
    start_sync_run,
)

__all__ = [
    "SENTINEL_VERSION",
    "build_sync_sentinel_report",
    "build_sync_sentinel_agent_v0",
    "SYNC_FAIL_FAST_EXIT_CODE",
    "SYNC_GUARD_BLOCKED_EXIT_CODE",
    "SyncFailFast",
    "SyncGuardBlocked",
    "ack_daily_sync_guard",
    "check_daily_sync_guard",
    "finish_sync_run",
    "record_daily_sync_summary",
    "record_sync_interrupt",
    "signal",
    "signals_from_budgets",
    "signals_from_open_alerts",
    "signals_from_overview",
    "signals_from_p6_79",
    "sort_signals",
    "start_sync_run",
]
