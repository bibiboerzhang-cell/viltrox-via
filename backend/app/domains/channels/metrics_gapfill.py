"""Official-channel daily-metrics gap-fill (deterministic Python backfill).

Source `vkpi_channel_metrics` (immutable daily snapshots) is read-only here; the
only write target is the isolated `vkpi_channel_metrics_filled` table. Official
accounts only — never touches viltrox_fit_score / rule_v0 / vkpi_kol_pool.

Classification per (channel, date) over each channel's synced date span:
  - synced row exists            -> source='synced',           confidence='高'
  - missing, prior+later synced  -> linear interpolation,       source='estimated_linear'
  - missing, only prior synced   -> carry forward prior values, source='carry_forward'
  - missing, no prior            -> SKIP (never fabricate leading values)
Deltas are intentionally not filled (per-snapshot semantics; meaningless here).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.db.connection import get_conn
from app.domains.channels.common import _is_official_channel_row, _int, _parse_json
from app.domains.channels.schema import ensure_vkpi_channels_schema

# Numeric metric columns carried into the filled table (deltas excluded by design).
_COUNT_FIELDS = ("followers", "posts_count", "total_views", "total_likes", "total_comments", "total_shares")
_METRIC_FIELDS = (*_COUNT_FIELDS, "engagement_rate")


def _to_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _official_channel_ids(conn: Any, channel_ids: list[int] | None) -> list[int]:
    rows = conn.execute(
        "SELECT id, metadata_json FROM vkpi_employee_channels "
        "WHERE deleted_at IS NULL AND status='active'"
    ).fetchall()
    wanted = {int(cid) for cid in channel_ids} if channel_ids else None
    out: list[int] = []
    for row in rows:
        cid = _int(row.get("id"))
        if not cid or not _is_official_channel_row(dict(row)):
            continue
        if wanted is not None and cid not in wanted:
            continue
        out.append(cid)
    return sorted(out)


def _synced_by_date(conn: Any, channel_id: int, start: date | None, end: date | None) -> dict[date, dict[str, Any]]:
    sql = (
        "SELECT snapshot_date, followers, posts_count, total_views, total_likes, "
        "total_comments, total_shares, engagement_rate "
        "FROM vkpi_channel_metrics WHERE channel_id=?"
    )
    params: list[Any] = [int(channel_id)]
    if start is not None:
        sql += " AND snapshot_date>=?"
        params.append(start.isoformat())
    if end is not None:
        sql += " AND snapshot_date<=?"
        params.append(end.isoformat())
    out: dict[date, dict[str, Any]] = {}
    for row in conn.execute(sql, params).fetchall():
        snap = _to_date(row.get("snapshot_date"))
        if snap is None:
            continue
        out[snap] = {
            "followers": _int(row.get("followers")),
            "posts_count": _int(row.get("posts_count")),
            "total_views": _int(row.get("total_views")),
            "total_likes": _int(row.get("total_likes")),
            "total_comments": _int(row.get("total_comments")),
            "total_shares": _int(row.get("total_shares")),
            "engagement_rate": row.get("engagement_rate"),
        }
    return out


def _interp(prior: dict[str, Any], later: dict[str, Any], prior_d: date, later_d: date, target: date) -> dict[str, Any]:
    span = (later_d - prior_d).days
    frac = (target - prior_d).days / span if span else 0.0
    out: dict[str, Any] = {}
    for field in _COUNT_FIELDS:
        lo, hi = _int(prior.get(field)), _int(later.get(field))
        out[field] = int(round(lo + (hi - lo) * frac))
    p_er, l_er = prior.get("engagement_rate"), later.get("engagement_rate")
    if p_er is None or l_er is None:
        out["engagement_rate"] = None
    else:
        out["engagement_rate"] = float(p_er) + (float(l_er) - float(p_er)) * frac
    return out


def _row(channel_id: int, snap: date, metrics: dict[str, Any], source: str, confidence: str, reason: str) -> dict[str, Any]:
    return {
        "channel_id": int(channel_id),
        "snapshot_date": snap.isoformat(),
        **{field: metrics.get(field) for field in _METRIC_FIELDS},
        "source": source,
        "confidence": confidence,
        "reason": reason,
    }


def _channel_series(channel_id: int, synced: dict[date, dict[str, Any]], start: date, end: date) -> list[dict[str, Any]]:
    if not synced:
        return []
    dates = sorted(synced)
    lo = max(start, dates[0])
    hi = min(end, dates[-1])
    if lo > hi:
        return []
    out: list[dict[str, Any]] = []
    cursor = lo
    while cursor <= hi:
        if cursor in synced:
            out.append(_row(channel_id, cursor, synced[cursor], "synced", "高", "original_snapshot"))
        else:
            prior_d = max((d for d in dates if d < cursor), default=None)
            later_d = min((d for d in dates if d > cursor), default=None)
            if prior_d is not None and later_d is not None:
                metrics = _interp(synced[prior_d], synced[later_d], prior_d, later_d, cursor)
                confidence = "中" if (later_d - prior_d).days <= 1 else "低"
                reason = f"linear_interp_{prior_d.isoformat()}_to_{later_d.isoformat()}"
                out.append(_row(channel_id, cursor, metrics, "estimated_linear", confidence, reason))
            elif prior_d is not None:
                reason = f"carry_fwd_from_{prior_d.isoformat()}"
                out.append(_row(channel_id, cursor, synced[prior_d], "carry_forward", "中", reason))
            # no prior neighbor -> SKIP (do not fabricate leading values)
        cursor += timedelta(days=1)
    return out


def _window_bounds(conn: Any, channel_ids: list[int], start: date | None, end: date | None) -> tuple[date | None, date | None]:
    if start is not None and end is not None:
        return start, end
    placeholders = ",".join("?" for _ in channel_ids)
    row = conn.execute(
        f"SELECT MIN(snapshot_date) AS lo, MAX(snapshot_date) AS hi "
        f"FROM vkpi_channel_metrics WHERE channel_id IN ({placeholders})",
        list(channel_ids),
    ).fetchone()
    lo = start or _to_date(row.get("lo") if row else None)
    hi = end or _to_date(row.get("hi") if row else None)
    return lo, hi


def compute_filled_series(
    conn: Any,
    channel_ids: list[int] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> list[dict[str, Any]]:
    """Pure read. Build the gap-filled series for active official channels."""
    ensure_vkpi_channels_schema()
    start = _to_date(start)
    end = _to_date(end)
    official_ids = _official_channel_ids(conn, channel_ids)
    if not official_ids:
        return []
    win_lo, win_hi = _window_bounds(conn, official_ids, start, end)
    if win_lo is None or win_hi is None or win_lo > win_hi:
        return []
    series: list[dict[str, Any]] = []
    for cid in official_ids:
        synced = _synced_by_date(conn, cid, win_lo, win_hi)
        series.extend(_channel_series(cid, synced, win_lo, win_hi))
    return series


def backfill_filled_table(conn: Any) -> dict[str, int]:
    """Full regenerate (idempotent): wipe + reinsert the entire filled series."""
    rows = compute_filled_series(conn)
    conn.execute("DELETE FROM vkpi_channel_metrics_filled")
    counts = {"synced": 0, "estimated_linear": 0, "carry_forward": 0, "total": 0}
    for row in rows:
        conn.execute(
            "INSERT INTO vkpi_channel_metrics_filled "
            "(channel_id, snapshot_date, followers, posts_count, total_views, total_likes, "
            "total_comments, total_shares, engagement_rate, source, confidence, reason) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row["channel_id"], row["snapshot_date"], row["followers"], row["posts_count"],
                row["total_views"], row["total_likes"], row["total_comments"], row["total_shares"],
                row["engagement_rate"], row["source"], row["confidence"], row["reason"],
            ),
        )
        counts[row["source"]] = counts.get(row["source"], 0) + 1
        counts["total"] += 1
    conn.commit()
    return counts


def gap_summary(conn: Any, window_days: int = 30) -> dict[str, Any]:
    """Counts by source/confidence plus the non-synced (channel, date, reason) gaps."""
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=max(0, int(window_days)) - 1) if window_days else None
    series = compute_filled_series(conn, start=start, end=end)
    by_source: dict[str, int] = {}
    by_confidence: dict[str, int] = {}
    gaps: list[dict[str, Any]] = []
    for row in series:
        by_source[row["source"]] = by_source.get(row["source"], 0) + 1
        by_confidence[row["confidence"]] = by_confidence.get(row["confidence"], 0) + 1
        if row["source"] != "synced":
            gaps.append({
                "channel_id": row["channel_id"],
                "snapshot_date": row["snapshot_date"],
                "source": row["source"],
                "confidence": row["confidence"],
                "reason": row["reason"],
            })
    return {
        "window_days": int(window_days),
        "window_start": start.isoformat() if start else None,
        "window_end": end.isoformat(),
        "total": len(series),
        "by_source": by_source,
        "by_confidence": by_confidence,
        "filled_count": len(gaps),
        "gaps": gaps,
    }


__all__ = ["compute_filled_series", "backfill_filled_table", "gap_summary"]
