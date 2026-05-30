"""Date cleanup helpers for dry-run rows."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any


EXCEL_EPOCH = datetime(1899, 12, 30)


def normalize_date(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raw = str(value).strip()
    if not raw:
        return ""
    if raw.replace(".", "", 1).isdigit():
        try:
            return (EXCEL_EPOCH + timedelta(days=float(raw))).date().isoformat()
        except (OverflowError, ValueError):
            return raw
    return raw.split(" ")[0].replace("/", "-")

