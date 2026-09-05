"""Pure read-coverage state for the three internal weekly-signal sources.

An empty, successfully read window is not a failed or unreported source. This
contract says nothing about external-market coverage or the quality of a signal.
"""
from __future__ import annotations

from typing import Any


SIGNAL_SOURCES = ("brand_pulse", "category_tracks", "market_voice")
READ_COMPLETE_STATUSES = frozenset(
    {"ok", "ready", "empty", "no_data_in_window", "no_brand_signal"}
)


def signal_read_state(sources: dict[str, Any], *, has_items: bool) -> str:
    """Return ok/empty only when every expected source reports a complete read."""
    complete = 0
    for name in SIGNAL_SOURCES:
        source = sources.get(name)
        status = str(source.get("status") or "").strip().lower() if isinstance(source, dict) else ""
        complete += status in READ_COMPLETE_STATUSES
    if complete == len(SIGNAL_SOURCES):
        return "ok" if has_items else "empty"
    if complete or has_items:
        return "partial"
    return "error"
