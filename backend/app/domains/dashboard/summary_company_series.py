"""Pure official-account time-series reconstruction for dashboard summaries.

The public facade remains in ``summary_company``.  Keeping the reconstruction
pure and split by metric makes the coverage contract independently testable
without changing the database-facing API.
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import date, datetime, timedelta
from typing import Any

from app.domains.dashboard.summary_rows import _row_value


_COMPANY_SERIES_METRICS = ("kol-count", "active-30d", "exposure", "engagement")
_COMPANY_SERIES_BASIS = {
    "kol-count": "Official accounts with a channel snapshot available as of each date.",
    "active-30d": (
        "Distinct official accounts with a post published or a positive post-metric "
        "delta observed in the rolling window ending on each date."
    ),
    "exposure": (
        "Rolling-window sum of positive views_delta_24h values observed for official "
        "accounts; missing daily rows are not carried forward."
    ),
    "engagement": (
        "100 * sum(as-of total_likes + total_comments) / sum(as-of total_views) "
        "across metric-complete official accounts."
    ),
}

SnapshotState = dict[str, Any]
DailySnapshots = dict[str, dict[date, tuple[tuple[str, int], SnapshotState]]]
SnapshotHistories = dict[str, tuple[list[date], list[SnapshotState]]]
MetricPoints = dict[str, list[dict[str, Any]]]
MetricStats = dict[str, list[dict[str, int]]]
MetricSourceDates = dict[str, set[date]]


def _series_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _channel_key(value: Any) -> str | None:
    if hasattr(value, "get"):
        value = value.get("channel_id", value.get("id"))
    if value is None or value == "":
        return None
    return str(value)


def _coverage_ratio(count: int, official_accounts: int) -> float | None:
    if official_accounts <= 0:
        return None
    return round(count / official_accounts, 6)


def _series_delta_pct(points: list[dict[str, Any]]) -> float | None:
    if len(points) < 2:
        return None
    first = float(points[0]["value"])
    last = float(points[-1]["value"])
    if first == 0:
        return None
    return round((last - first) / abs(first) * 100, 6)


def _series_coverage(
    stats: list[dict[str, int]],
    *,
    official_accounts: int,
    point_days: int,
    requires_baseline: bool,
) -> dict[str, Any]:
    latest = stats[-1] if stats else {
        "eligible": 0,
        "direct": 0,
        "carried": 0,
        "baseline_direct": 0,
        "baseline_carried": 0,
    }
    eligible_counts = [item["eligible"] for item in stats]
    minimum = min(eligible_counts) if eligible_counts else 0
    coverage = {
        "official_accounts": official_accounts,
        "calendar_days": len(stats),
        "point_days": point_days,
        "eligible_accounts_latest": latest["eligible"],
        "eligible_ratio_latest": _coverage_ratio(latest["eligible"], official_accounts),
        "eligible_accounts_min": minimum,
        "eligible_ratio_min": _coverage_ratio(minimum, official_accounts),
        "complete_days": sum(
            1 for count in eligible_counts if official_accounts > 0 and count == official_accounts
        ),
        "direct_snapshot_accounts_latest": latest["direct"],
        "carried_forward_accounts_latest": latest["carried"],
        "baseline_direct_accounts_latest": None,
        "baseline_carried_forward_accounts_latest": None,
    }
    if requires_baseline:
        coverage["baseline_direct_accounts_latest"] = latest["baseline_direct"]
        coverage["baseline_carried_forward_accounts_latest"] = latest["baseline_carried"]
    return coverage


def _empty_company_metric_series(
    official_accounts: int,
    *,
    window_days: int,
) -> dict[str, dict[str, Any]]:
    return {
        metric: {
            "points": [],
            "delta_pct": None,
            "window_days": window_days,
            "basis": {
                "definition": _COMPANY_SERIES_BASIS[metric],
                "state_method": (
                    "rolling post evidence; no carry-forward"
                    if metric == "active-30d"
                    else "rolling positive daily deltas; no carry-forward"
                    if metric == "exposure"
                    else "per-channel as-of carry-forward"
                ),
                "baseline_guard": None,
                "delta_method": "first-to-last point percentage; null when unavailable or the first value is zero",
                "tables": (
                    ["vkpi_employee_channels", "vkpi_channel_post_metrics"]
                    if metric == "active-30d"
                    else ["vkpi_employee_channels", "vkpi_channel_metrics"]
                ),
            },
            "coverage": _series_coverage(
                [],
                official_accounts=official_accounts,
                point_days=0,
                requires_baseline=False,
            ),
            "source_dates": [],
        }
        for metric in _COMPANY_SERIES_METRICS
    }


def _normalize_channel_keys(official_channel_ids: list[Any]) -> tuple[list[str], set[str]]:
    channel_keys: list[str] = []
    seen_keys: set[str] = set()
    for raw_channel_id in official_channel_ids:
        key = _channel_key(raw_channel_id)
        if key is not None and key not in seen_keys:
            channel_keys.append(key)
            seen_keys.add(key)
    return channel_keys, seen_keys


def _build_daily_snapshots(
    channel_keys: list[str],
    seen_keys: set[str],
    snapshot_rows: list[dict[str, Any]],
) -> DailySnapshots:
    # UNIQUE(channel_id, snapshot_date) is the production contract. The rank
    # still makes this pure function deterministic for legacy/fixture duplicates.
    daily: DailySnapshots = {key: {} for key in channel_keys}
    for row in snapshot_rows:
        key = _channel_key(_row_value(row, "channel_id"))
        snapshot_date = _series_date(_row_value(row, "snapshot_date"))
        if key not in seen_keys or snapshot_date is None:
            continue
        rank = (
            str(_row_value(row, "captured_at") or ""),
            _optional_int(_row_value(row, "id")) or -1,
        )
        state = {
            "source_date": snapshot_date,
            "posts_count": _optional_int(_row_value(row, "posts_count")),
            "views_delta_24h": _optional_int(_row_value(row, "views_delta_24h")),
            "total_views": _optional_int(_row_value(row, "total_views")),
            "total_likes": _optional_int(_row_value(row, "total_likes")),
            "total_comments": _optional_int(_row_value(row, "total_comments")),
        }
        existing = daily[key].get(snapshot_date)
        if existing is None or rank >= existing[0]:
            daily[key][snapshot_date] = (rank, state)
    return daily


def _build_snapshot_histories(
    channel_keys: list[str],
    daily: DailySnapshots,
) -> tuple[SnapshotHistories, list[date]]:
    histories: SnapshotHistories = {}
    all_snapshot_dates: list[date] = []
    for key in channel_keys:
        ordered = sorted(daily[key].items())
        dates = [snapshot_date for snapshot_date, _ in ordered]
        states = [ranked_state[1] for _, ranked_state in ordered]
        for index, state in enumerate(states):
            state["has_prior_snapshot"] = index > 0
        histories[key] = (dates, states)
        all_snapshot_dates.extend(dates)
    return histories, all_snapshot_dates


def _state_as_of(
    histories: SnapshotHistories,
    key: str,
    target: date,
) -> SnapshotState | None:
    dates, states = histories[key]
    index = bisect_right(dates, target) - 1
    return states[index] if index >= 0 else None


def _audit_stats(
    eligible: list[str],
    current: dict[str, SnapshotState],
    target: date,
    baseline: dict[str, SnapshotState] | None = None,
    baseline_date: date | None = None,
) -> dict[str, int]:
    direct = sum(1 for key in eligible if current[key]["source_date"] == target)
    baseline_direct = 0
    if baseline is not None and baseline_date is not None:
        baseline_direct = sum(
            1 for key in eligible if baseline[key]["source_date"] == baseline_date
        )
    return {
        "eligible": len(eligible),
        "direct": direct,
        "carried": len(eligible) - direct,
        "baseline_direct": baseline_direct,
        "baseline_carried": len(eligible) - baseline_direct if baseline is not None else 0,
    }


def _append_point(
    metric: str,
    target: date,
    value: int | float | None,
    eligible: list[str],
    current: dict[str, SnapshotState],
    metric_stats: dict[str, int],
    *,
    official_accounts: int,
    points: MetricPoints,
    stats: MetricStats,
    used_dates: MetricSourceDates,
    baseline: dict[str, SnapshotState] | None = None,
) -> None:
    stats[metric].append(metric_stats)
    if value is None or not eligible:
        return
    point = {
        "date": target.isoformat(),
        "value": value,
        "covered_accounts": metric_stats["eligible"],
        "coverage_pct": _coverage_ratio(metric_stats["eligible"], official_accounts),
        "direct_snapshot_accounts": metric_stats["direct"],
        "carried_forward_accounts": metric_stats["carried"],
    }
    if baseline is not None:
        point["baseline_direct_accounts"] = metric_stats["baseline_direct"]
        point["baseline_carried_forward_accounts"] = metric_stats["baseline_carried"]
    points[metric].append(point)
    used_dates[metric].update(current[key]["source_date"] for key in eligible)
    if baseline is not None:
        used_dates[metric].update(baseline[key]["source_date"] for key in eligible)


def _normalize_post_rows(
    post_rows: list[dict[str, Any]] | None,
    seen_keys: set[str],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in post_rows or []:
        key = _channel_key(_row_value(row, "channel_id"))
        snapshot_date = _series_date(_row_value(row, "snapshot_date"))
        posted_at = _series_date(_row_value(row, "posted_at"))
        if key not in seen_keys or (snapshot_date is None and posted_at is None):
            continue
        positive_flag = _row_value(row, "has_positive_delta")
        has_positive_delta = (
            str(positive_flag).strip().lower() in {"1", "true", "t", "yes"}
            if positive_flag is not None
            else any(
                (_optional_int(_row_value(row, field)) or 0) > 0
                for field in ("views_delta", "likes_delta", "comments_delta")
            )
        )
        normalized.append(
            {
                "channel_key": key,
                "snapshot_date": snapshot_date,
                "posted_at": posted_at,
                "has_positive_delta": has_positive_delta,
            }
        )
    return normalized


def _record_roster_metric(
    target: date,
    current: dict[str, SnapshotState],
    *,
    official_accounts: int,
    points: MetricPoints,
    stats: MetricStats,
    used_dates: MetricSourceDates,
) -> None:
    roster_keys = list(current)
    roster_stats = _audit_stats(roster_keys, current, target)
    _append_point(
        "kol-count",
        target,
        len(roster_keys) if roster_keys else None,
        roster_keys,
        current,
        roster_stats,
        official_accounts=official_accounts,
        points=points,
        stats=stats,
        used_dates=used_dates,
    )


def _record_active_metric(
    target: date,
    window_start: date,
    normalized_post_rows: list[dict[str, Any]],
    *,
    official_accounts: int,
    points: MetricPoints,
    stats: MetricStats,
    used_dates: MetricSourceDates,
) -> None:
    active_source_rows = [
        row
        for row in normalized_post_rows
        if (
            row["posted_at"] is not None and window_start <= row["posted_at"] <= target
        )
        or (
            row["snapshot_date"] is not None
            and window_start <= row["snapshot_date"] <= target
            and row["has_positive_delta"]
        )
    ]
    active_keys = sorted({row["channel_key"] for row in active_source_rows})
    observed_post_keys = {
        row["channel_key"]
        for row in normalized_post_rows
        if (row["snapshot_date"] is not None and row["snapshot_date"] <= target)
        or (row["posted_at"] is not None and row["posted_at"] <= target)
    }
    active_direct_keys = {
        row["channel_key"]
        for row in normalized_post_rows
        if row["snapshot_date"] == target or row["posted_at"] == target
    }
    stats["active-30d"].append(
        {
            "eligible": len(observed_post_keys),
            "direct": len(active_direct_keys),
            "carried": 0,
            "baseline_direct": 0,
            "baseline_carried": 0,
        }
    )
    if not normalized_post_rows:
        return
    points["active-30d"].append(
        {
            "date": target.isoformat(),
            "value": len(active_keys),
            "covered_accounts": len(observed_post_keys),
            "coverage_pct": _coverage_ratio(len(observed_post_keys), official_accounts),
            "direct_snapshot_accounts": len(active_direct_keys),
            "carried_forward_accounts": 0,
        }
    )
    for row in active_source_rows:
        source_date = row["snapshot_date"] or row["posted_at"]
        if source_date is not None:
            used_dates["active-30d"].add(source_date)


def _record_exposure_metric(
    target: date,
    window_start: date,
    channel_keys: list[str],
    daily: DailySnapshots,
    *,
    official_accounts: int,
    points: MetricPoints,
    stats: MetricStats,
    used_dates: MetricSourceDates,
) -> None:
    exposure_states: list[SnapshotState] = []
    exposure_keys_set: set[str] = set()
    exposure_direct_keys: set[str] = set()
    for key in channel_keys:
        for snapshot_date, ranked_state in daily[key].items():
            if not window_start <= snapshot_date <= target:
                continue
            exposure_states.append(ranked_state[1])
            exposure_keys_set.add(key)
            if snapshot_date == target:
                exposure_direct_keys.add(key)
            used_dates["exposure"].add(snapshot_date)
    exposure_keys = sorted(exposure_keys_set)
    exposure_value = (
        sum(max(state["views_delta_24h"] or 0, 0) for state in exposure_states)
        if exposure_states
        else None
    )
    stats["exposure"].append(
        {
            "eligible": len(exposure_keys),
            "direct": len(exposure_direct_keys),
            "carried": 0,
            "baseline_direct": 0,
            "baseline_carried": 0,
        }
    )
    if exposure_value is None or not exposure_keys:
        return
    points["exposure"].append(
        {
            "date": target.isoformat(),
            "value": exposure_value,
            "covered_accounts": len(exposure_keys),
            "coverage_pct": _coverage_ratio(len(exposure_keys), official_accounts),
            "direct_snapshot_accounts": len(exposure_direct_keys),
            "carried_forward_accounts": 0,
        }
    )


def _record_engagement_metric(
    target: date,
    current: dict[str, SnapshotState],
    *,
    official_accounts: int,
    points: MetricPoints,
    stats: MetricStats,
    used_dates: MetricSourceDates,
) -> None:
    engagement_keys = [
        key
        for key, state in current.items()
        if all(
            state[field] is not None
            for field in ("total_views", "total_likes", "total_comments")
        )
    ]
    engagement_stats = _audit_stats(engagement_keys, current, target)
    engagement_views = sum(current[key]["total_views"] for key in engagement_keys)
    engagement_value = None
    if engagement_keys and engagement_views > 0:
        interactions = sum(
            current[key]["total_likes"] + current[key]["total_comments"]
            for key in engagement_keys
        )
        engagement_value = round(interactions / engagement_views * 100, 6)
    _append_point(
        "engagement",
        target,
        engagement_value,
        engagement_keys,
        current,
        engagement_stats,
        official_accounts=official_accounts,
        points=points,
        stats=stats,
        used_dates=used_dates,
    )


def _finalize_metric_series(
    *,
    official_accounts: int,
    days: int,
    points: MetricPoints,
    stats: MetricStats,
    used_dates: MetricSourceDates,
) -> dict[str, dict[str, Any]]:
    result = _empty_company_metric_series(official_accounts, window_days=days)
    for metric in _COMPANY_SERIES_METRICS:
        result[metric]["points"] = points[metric]
        result[metric]["delta_pct"] = _series_delta_pct(points[metric])
        result[metric]["coverage"] = _series_coverage(
            stats[metric],
            official_accounts=official_accounts,
            point_days=len(points[metric]),
            requires_baseline=False,
        )
        result[metric]["source_dates"] = [
            source_date.isoformat() for source_date in sorted(used_dates[metric])
        ]
    return result


def _build_company_metric_series_from_snapshots(
    official_channel_ids: list[Any],
    snapshot_rows: list[dict[str, Any]],
    *,
    post_rows: list[dict[str, Any]] | None = None,
    window_days: int = 30,
) -> dict[str, dict[str, Any]]:
    """Build auditable official-account series without querying or mutating state."""
    days = max(1, int(window_days or 30))
    channel_keys, seen_keys = _normalize_channel_keys(official_channel_ids)
    official_accounts = len(channel_keys)
    daily = _build_daily_snapshots(channel_keys, seen_keys, snapshot_rows)
    histories, all_snapshot_dates = _build_snapshot_histories(channel_keys, daily)
    if not all_snapshot_dates:
        return _empty_company_metric_series(official_accounts, window_days=days)

    latest_date = max(all_snapshot_dates)
    target_dates = [
        latest_date - timedelta(days=offset) for offset in range(days - 1, -1, -1)
    ]
    points: MetricPoints = {metric: [] for metric in _COMPANY_SERIES_METRICS}
    stats: MetricStats = {metric: [] for metric in _COMPANY_SERIES_METRICS}
    used_dates: MetricSourceDates = {metric: set() for metric in _COMPANY_SERIES_METRICS}
    normalized_post_rows = _normalize_post_rows(post_rows, seen_keys)

    for target in target_dates:
        current = {
            key: state
            for key in channel_keys
            if (state := _state_as_of(histories, key, target)) is not None
        }
        window_start = target - timedelta(days=days - 1)
        _record_roster_metric(
            target,
            current,
            official_accounts=official_accounts,
            points=points,
            stats=stats,
            used_dates=used_dates,
        )
        _record_active_metric(
            target,
            window_start,
            normalized_post_rows,
            official_accounts=official_accounts,
            points=points,
            stats=stats,
            used_dates=used_dates,
        )
        _record_exposure_metric(
            target,
            window_start,
            channel_keys,
            daily,
            official_accounts=official_accounts,
            points=points,
            stats=stats,
            used_dates=used_dates,
        )
        _record_engagement_metric(
            target,
            current,
            official_accounts=official_accounts,
            points=points,
            stats=stats,
            used_dates=used_dates,
        )
    return _finalize_metric_series(
        official_accounts=official_accounts,
        days=days,
        points=points,
        stats=stats,
        used_dates=used_dates,
    )
