"""Trend domain facade."""

from app.domains.trends.trend_detection import (
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_ROW_LIMIT,
    DEFAULT_TOP_SIGNALS,
    DETECTION_VERSION,
    RULES,
    build_trend_detection_report,
)
from app.domains.trends.trend_detection_use_case import build_trend_detection_v0

__all__ = [
    "DEFAULT_LOOKBACK_DAYS",
    "DEFAULT_ROW_LIMIT",
    "DEFAULT_TOP_SIGNALS",
    "DETECTION_VERSION",
    "RULES",
    "build_trend_detection_report",
    "build_trend_detection_v0",
]
