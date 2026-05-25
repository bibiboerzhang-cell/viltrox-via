"""Intelligence domain facade."""

from app.domains.intelligence.today_signals import (
    DEFAULT_LIMIT,
    DEFAULT_LOOKBACK_HOURS,
    DIGEST_VERSION,
    build_today_new_signals_report,
)

__all__ = [
    "DEFAULT_LIMIT",
    "DEFAULT_LOOKBACK_HOURS",
    "DIGEST_VERSION",
    "build_today_new_signals_report",
]
