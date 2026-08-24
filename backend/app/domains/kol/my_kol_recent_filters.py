"""Small pure helpers for MY KOL content-wall keyset and scope filters."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.domains.kol.my_kol_board_ext_sql import (
    RECENT_FILTER_PARAM_COUNT,
    RECENT_KEYSET_PARAM_COUNT,
)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def recent_keyset_params(before: tuple[str | None, int] | None) -> tuple[Any, ...]:
    """Return the seven parameters used by the recent-video keyset clause."""
    if not before or _int(before[1]) <= 0:
        params: tuple[Any, ...] = (False, None, None, None, 0, None, 0)
    else:
        published_at, evidence_id = before
        key = str(published_at) if published_at not in (None, "") else None
        params = (True, key, key, key, int(evidence_id), key, int(evidence_id))
    assert len(params) == RECENT_KEYSET_PARAM_COUNT
    return params


def recent_filter_params(
    now: datetime,
    days: int = 0,
    kol_pool_id: int = 0,
    since: datetime | None = None,
) -> tuple[Any, ...]:
    """Return time and KOL filter parameters; zero values mean all."""
    safe_days = max(0, min(_int(days), 365))
    now_utc = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
    floor = now_utc - timedelta(days=safe_days)
    candidate = since.replace(tzinfo=timezone.utc) if since and since.tzinfo is None else since
    candidate = candidate.astimezone(timezone.utc) if candidate else None
    # ``since`` is a server-issued pagination anchor, not permission to widen
    # the advertised window. Keep a small request-to-request clock allowance;
    # ancient/future caller values are clamped back to the declared window.
    if safe_days and candidate:
        cutoff = candidate if floor - timedelta(minutes=5) <= candidate <= now_utc else floor
    else:
        cutoff = floor
    since_text = cutoff.isoformat() if safe_days else None
    pool_id = max(0, _int(kol_pool_id))
    params = (since_text, since_text, pool_id, pool_id)
    assert len(params) == RECENT_FILTER_PARAM_COUNT
    return params


def recent_filter_payload(
    days: int = 0,
    kol_pool_id: int = 0,
    since: str | None = None,
) -> dict[str, int | str | None]:
    return {
        "days": max(0, min(_int(days), 365)),
        "kol_pool_id": max(0, _int(kol_pool_id)) or None,
        "since": str(since or "") or None,
    }
