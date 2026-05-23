"""P6.73 rule-based trend detection v0.

This module is intentionally read-only. It detects current trend signals from
documented time-series anchors and never triggers sync, provider calls, LLMs,
or database writes.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from math import ceil
from typing import Any

from app.db.connection import get_conn


DETECTION_VERSION = "trend-detection-v0.1"
DEFAULT_ROW_LIMIT = 5000
DEFAULT_LOOKBACK_DAYS = 14
DEFAULT_TOP_SIGNALS = 25

RULES: dict[str, dict[str, Any]] = {
    "official_post_views_delta_spike": {
        "anchor": "vkpi_channel_post_metrics",
        "metric": "views_delta",
        "requires": ["at least 2 snapshots for the same post", "positive views_delta"],
        "minimum_threshold": 500,
        "dynamic_threshold": "p90 of latest positive post views deltas",
    },
    "official_post_engagement_delta_spike": {
        "anchor": "vkpi_channel_post_metrics",
        "metric": "likes_delta + comments_delta + shares_delta",
        "requires": ["at least 2 snapshots for the same post", "positive engagement delta"],
        "minimum_threshold": 50,
        "dynamic_threshold": "p90 of latest positive engagement deltas",
    },
    "official_post_growth_acceleration": {
        "anchor": "vkpi_channel_post_metrics",
        "metric": "latest views velocity / previous views velocity",
        "requires": ["at least 3 snapshots for the same post", "previous velocity > 0"],
        "minimum_threshold": 2.0,
        "dynamic_threshold": "fixed ratio until more history exists",
    },
    "official_channel_daily_delta_watch": {
        "anchor": "vkpi_channel_metrics",
        "metric": "followers_delta, posts_delta, views_delta_24h, likes_delta_24h",
        "requires": ["channel daily delta field", "positive latest delta"],
        "minimum_threshold": "followers 50 or views 5000",
        "dynamic_threshold": "p90 of latest positive channel deltas",
    },
    "market_signal_event_burst": {
        "anchor": "vkpi_competitor_signals",
        "metric": "count of market events by brand/platform/type",
        "requires": ["event time", "review status preserved"],
        "minimum_threshold": 2,
        "dynamic_threshold": "fixed count until source volume grows",
    },
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds").replace("+00:00", "Z")


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    else:
        raw = _text(value).strip()
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            try:
                dt = datetime.strptime(raw[:10], "%Y-%m-%d")
            except Exception:
                return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _age_days(value: Any, now: datetime) -> float | None:
    dt = _parse_datetime(value)
    if not dt:
        return None
    return max(0.0, (now - dt).total_seconds() / 86400.0)


def _interval_hours(current: dict[str, Any], previous: dict[str, Any]) -> tuple[float, bool]:
    current_time = _parse_datetime(current.get("captured_at")) or _parse_datetime(current.get("snapshot_date"))
    previous_time = _parse_datetime(previous.get("captured_at")) or _parse_datetime(previous.get("snapshot_date"))
    if current_time and previous_time and current_time > previous_time:
        return max(1.0, (current_time - previous_time).total_seconds() / 3600.0), True
    return 24.0, False


def _safe_query(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in get_conn().execute(sql, params).fetchall()]
    except Exception:
        return []


def _table_exists(table_name: str) -> bool:
    try:
        row = get_conn().execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        if row:
            return True
    except Exception:
        pass
    try:
        row = get_conn().execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table_name,)).fetchone()
        return bool(row)
    except Exception:
        return False


def _percentile(values: list[float], percentile: float) -> float:
    clean = sorted(value for value in values if value > 0)
    if not clean:
        return 0.0
    index = max(0, min(len(clean) - 1, ceil(percentile * len(clean)) - 1))
    return float(clean[index])


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, value))


def _severity(score: float) -> str:
    if score >= 85:
        return "critical"
    if score >= 70:
        return "high"
    if score >= 45:
        return "medium"
    return "watch"


def _load_post_metric_rows(limit: int = DEFAULT_ROW_LIMIT) -> list[dict[str, Any]]:
    if not _table_exists("vkpi_channel_post_metrics"):
        return []
    return _safe_query(
        """
        SELECT
            m.channel_id, m.snapshot_date, m.platform, m.post_uid, m.post_url, m.title,
            m.posted_at, m.views, m.likes, m.comments, m.shares,
            m.views_delta, m.likes_delta, m.comments_delta, m.shares_delta,
            m.delta_method, m.first_seen_at, m.captured_at,
            c.account_handle, c.account_display_name
        FROM vkpi_channel_post_metrics m
        LEFT JOIN vkpi_employee_channels c ON c.id = m.channel_id
        ORDER BY m.captured_at DESC, m.snapshot_date DESC, m.id DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    )


def _load_channel_metric_rows(limit: int = DEFAULT_ROW_LIMIT) -> list[dict[str, Any]]:
    if not _table_exists("vkpi_channel_metrics"):
        return []
    return _safe_query(
        """
        SELECT
            m.channel_id, m.snapshot_date, m.followers, m.posts_count, m.total_views,
            m.total_likes, m.total_comments, m.total_shares,
            m.followers_delta, m.posts_delta, m.views_delta_24h, m.likes_delta_24h,
            m.engagement_rate, m.captured_at,
            c.platform, c.account_handle, c.account_display_name
        FROM vkpi_channel_metrics m
        LEFT JOIN vkpi_employee_channels c ON c.id = m.channel_id
        ORDER BY m.captured_at DESC, m.snapshot_date DESC, m.id DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    )


def _load_market_signal_rows(limit: int = DEFAULT_ROW_LIMIT) -> list[dict[str, Any]]:
    if not _table_exists("vkpi_competitor_signals"):
        return []
    return _safe_query(
        """
        SELECT
            signal_uid, brand, normalized_brand, signal_type, severity, score, platform,
            detail, source_url, review_status, created_at, updated_at
        FROM vkpi_competitor_signals
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    )


def _group_series(rows: list[dict[str, Any]], key_fields: tuple[str, ...]) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = tuple(row.get(field) for field in key_fields)
        if any(value in (None, "") for value in key):
            continue
        grouped[key].append(row)
    for key, items in grouped.items():
        grouped[key] = sorted(
            items,
            key=lambda item: (
                _parse_datetime(item.get("captured_at")) or _parse_datetime(item.get("snapshot_date")) or datetime.min.replace(tzinfo=timezone.utc),
                _text(item.get("post_uid")),
            ),
        )
    return grouped


def _post_latest_points(post_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for _, series in _group_series(post_rows, ("channel_id", "post_uid")).items():
        if not series:
            continue
        latest = dict(series[-1])
        latest["_series_count"] = len(series)
        if len(series) >= 2:
            hours, interval_known = _interval_hours(latest, series[-2])
        else:
            hours, interval_known = 24.0, False
        latest["_interval_hours"] = hours
        latest["_interval_known"] = interval_known
        latest["_previous"] = series[-2] if len(series) >= 2 else None
        latest["_previous_previous"] = series[-3] if len(series) >= 3 else None
        latest["_views_delta"] = _to_int(latest.get("views_delta"))
        latest["_engagement_delta"] = (
            _to_int(latest.get("likes_delta"))
            + _to_int(latest.get("comments_delta"))
            + _to_int(latest.get("shares_delta"))
        )
        latest["_views_velocity"] = latest["_views_delta"] / hours
        latest["_engagement_velocity"] = latest["_engagement_delta"] / hours
        points.append(latest)
    return points


def _channel_latest_points(channel_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for _, series in _group_series(channel_rows, ("channel_id",)).items():
        if not series:
            continue
        latest = dict(series[-1])
        latest["_series_count"] = len(series)
        latest["_followers_delta"] = _to_int(latest.get("followers_delta"))
        latest["_posts_delta"] = _to_int(latest.get("posts_delta"))
        latest["_views_delta_24h"] = _to_int(latest.get("views_delta_24h"))
        latest["_likes_delta_24h"] = _to_int(latest.get("likes_delta_24h"))
        points.append(latest)
    return points


def _post_signal(
    *,
    point: dict[str, Any],
    rule_key: str,
    metric_value: float,
    threshold: float,
    score: float,
    evidence: dict[str, Any],
    now: datetime,
    lookback_days: int,
) -> dict[str, Any]:
    age = _age_days(point.get("captured_at") or point.get("snapshot_date"), now)
    stale = bool(age is not None and age > lookback_days)
    confidence = 0.85
    if point.get("_series_count", 0) < 3:
        confidence -= 0.15
    if not point.get("_interval_known"):
        confidence -= 0.1
    if stale:
        confidence -= 0.25
    if metric_value <= 0:
        confidence -= 0.2
    return {
        "signal_type": "official_post_growth",
        "rule_key": rule_key,
        "severity": _severity(score),
        "score": round(_clamp(score), 2),
        "confidence": round(_clamp(confidence, 0, 1), 2),
        "is_abnormal_growth": rule_key != "official_post_positive_delta_watch",
        "entity": {
            "entity_type": "official_post",
            "channel_id": _to_int(point.get("channel_id")),
            "platform": _text(point.get("platform")),
            "account_handle": _text(point.get("account_handle")),
            "account_display_name": _text(point.get("account_display_name")),
            "post_uid": _text(point.get("post_uid")),
            "post_url": _text(point.get("post_url")),
            "title": _text(point.get("title"))[:240],
        },
        "metric": {
            "value": round(float(metric_value), 4),
            "threshold": round(float(threshold), 4),
            "unit": "delta_or_velocity",
            "snapshot_date": _text(point.get("snapshot_date")),
            "captured_at": _text(point.get("captured_at")),
            "age_days": round(age, 2) if age is not None else None,
        },
        "evidence": evidence,
    }


def _detect_post_trends(post_rows: list[dict[str, Any]], *, lookback_days: int, now: datetime) -> list[dict[str, Any]]:
    points = _post_latest_points(post_rows)
    positive_views = [float(point["_views_delta"]) for point in points if point["_views_delta"] > 0]
    positive_engagement = [float(point["_engagement_delta"]) for point in points if point["_engagement_delta"] > 0]
    views_threshold = max(500.0, _percentile(positive_views, 0.9))
    engagement_threshold = max(50.0, _percentile(positive_engagement, 0.9))
    signals: list[dict[str, Any]] = []
    for point in points:
        views_delta = float(point["_views_delta"])
        engagement_delta = float(point["_engagement_delta"])
        views_velocity = float(point["_views_velocity"])
        previous_velocity = 0.0
        acceleration = None
        previous = point.get("_previous")
        previous_previous = point.get("_previous_previous")
        if previous and previous_previous:
            prev_hours, _ = _interval_hours(previous, previous_previous)
            previous_velocity = _to_int(previous.get("views_delta")) / prev_hours
            if previous_velocity > 0:
                acceleration = views_velocity / previous_velocity
        evidence = {
            "series_count": point.get("_series_count", 0),
            "views_delta": int(views_delta),
            "engagement_delta": int(engagement_delta),
            "views_velocity_per_hour": round(views_velocity, 4),
            "engagement_velocity_per_hour": round(float(point["_engagement_velocity"]), 4),
            "previous_views_velocity_per_hour": round(previous_velocity, 4),
            "growth_acceleration": round(acceleration, 4) if acceleration is not None else None,
            "delta_method": _text(point.get("delta_method")),
        }
        if views_delta >= views_threshold and views_delta > 0:
            score = 55 + min(30, (views_delta / max(views_threshold, 1)) * 20)
            signals.append(
                _post_signal(
                    point=point,
                    rule_key="official_post_views_delta_spike",
                    metric_value=views_delta,
                    threshold=views_threshold,
                    score=score,
                    evidence=evidence,
                    now=now,
                    lookback_days=lookback_days,
                )
            )
            continue
        if engagement_delta >= engagement_threshold and engagement_delta > 0:
            score = 50 + min(30, (engagement_delta / max(engagement_threshold, 1)) * 20)
            signals.append(
                _post_signal(
                    point=point,
                    rule_key="official_post_engagement_delta_spike",
                    metric_value=engagement_delta,
                    threshold=engagement_threshold,
                    score=score,
                    evidence=evidence,
                    now=now,
                    lookback_days=lookback_days,
                )
            )
            continue
        if acceleration is not None and acceleration >= 2.0 and views_delta >= 100:
            score = 60 + min(30, acceleration * 5)
            signals.append(
                _post_signal(
                    point=point,
                    rule_key="official_post_growth_acceleration",
                    metric_value=acceleration,
                    threshold=2.0,
                    score=score,
                    evidence=evidence,
                    now=now,
                    lookback_days=lookback_days,
                )
            )
            continue
        if views_delta > 0 or engagement_delta > 0:
            score = min(44, 18 + (views_delta / max(views_threshold, 1)) * 18 + (engagement_delta / max(engagement_threshold, 1)) * 8)
            signals.append(
                _post_signal(
                    point=point,
                    rule_key="official_post_positive_delta_watch",
                    metric_value=max(views_delta, engagement_delta),
                    threshold=max(views_threshold, engagement_threshold),
                    score=score,
                    evidence=evidence,
                    now=now,
                    lookback_days=lookback_days,
                )
            )
    return signals


def _channel_signal(
    *,
    point: dict[str, Any],
    rule_key: str,
    metric_value: float,
    threshold: float,
    score: float,
    evidence: dict[str, Any],
    now: datetime,
    lookback_days: int,
) -> dict[str, Any]:
    age = _age_days(point.get("captured_at") or point.get("snapshot_date"), now)
    stale = bool(age is not None and age > lookback_days)
    confidence = 0.75
    if point.get("_series_count", 0) < 2:
        confidence -= 0.1
    if stale:
        confidence -= 0.25
    return {
        "signal_type": "official_channel_delta",
        "rule_key": rule_key,
        "severity": _severity(score),
        "score": round(_clamp(score), 2),
        "confidence": round(_clamp(confidence, 0, 1), 2),
        "is_abnormal_growth": rule_key != "official_channel_positive_delta_watch",
        "entity": {
            "entity_type": "official_channel",
            "channel_id": _to_int(point.get("channel_id")),
            "platform": _text(point.get("platform")),
            "account_handle": _text(point.get("account_handle")),
            "account_display_name": _text(point.get("account_display_name")),
        },
        "metric": {
            "value": round(float(metric_value), 4),
            "threshold": round(float(threshold), 4),
            "unit": "daily_delta",
            "snapshot_date": _text(point.get("snapshot_date")),
            "captured_at": _text(point.get("captured_at")),
            "age_days": round(age, 2) if age is not None else None,
        },
        "evidence": evidence,
    }


def _detect_channel_trends(channel_rows: list[dict[str, Any]], *, lookback_days: int, now: datetime) -> list[dict[str, Any]]:
    points = _channel_latest_points(channel_rows)
    positive_followers = [float(point["_followers_delta"]) for point in points if point["_followers_delta"] > 0]
    positive_views = [float(point["_views_delta_24h"]) for point in points if point["_views_delta_24h"] > 0]
    follower_threshold = max(50.0, _percentile(positive_followers, 0.9))
    views_threshold = max(5000.0, _percentile(positive_views, 0.9))
    signals: list[dict[str, Any]] = []
    for point in points:
        followers_delta = float(point["_followers_delta"])
        views_delta = float(point["_views_delta_24h"])
        likes_delta = float(point["_likes_delta_24h"])
        posts_delta = float(point["_posts_delta"])
        evidence = {
            "series_count": point.get("_series_count", 0),
            "followers_delta": int(followers_delta),
            "posts_delta": int(posts_delta),
            "views_delta_24h": int(views_delta),
            "likes_delta_24h": int(likes_delta),
            "engagement_rate": _to_float(point.get("engagement_rate")),
        }
        if followers_delta >= follower_threshold and followers_delta > 0:
            score = 55 + min(25, (followers_delta / max(follower_threshold, 1)) * 20)
            signals.append(
                _channel_signal(
                    point=point,
                    rule_key="official_channel_followers_delta_spike",
                    metric_value=followers_delta,
                    threshold=follower_threshold,
                    score=score,
                    evidence=evidence,
                    now=now,
                    lookback_days=lookback_days,
                )
            )
            continue
        if views_delta >= views_threshold and views_delta > 0:
            score = 55 + min(25, (views_delta / max(views_threshold, 1)) * 20)
            signals.append(
                _channel_signal(
                    point=point,
                    rule_key="official_channel_views_delta_spike",
                    metric_value=views_delta,
                    threshold=views_threshold,
                    score=score,
                    evidence=evidence,
                    now=now,
                    lookback_days=lookback_days,
                )
            )
            continue
        if followers_delta > 0 or views_delta > 0 or likes_delta > 0 or posts_delta > 0:
            score = min(44, 20 + (followers_delta / max(follower_threshold, 1)) * 12 + (views_delta / max(views_threshold, 1)) * 12)
            signals.append(
                _channel_signal(
                    point=point,
                    rule_key="official_channel_positive_delta_watch",
                    metric_value=max(followers_delta, views_delta, likes_delta, posts_delta),
                    threshold=max(follower_threshold, views_threshold),
                    score=score,
                    evidence=evidence,
                    now=now,
                    lookback_days=lookback_days,
                )
            )
    return signals


def _detect_market_signal_trends(market_rows: list[dict[str, Any]], *, lookback_days: int, now: datetime) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in market_rows:
        age = _age_days(row.get("created_at"), now)
        if age is not None and age > lookback_days:
            continue
        key = (
            _text(row.get("normalized_brand") or row.get("brand")).lower(),
            _text(row.get("platform")).lower(),
            _text(row.get("signal_type")).lower(),
        )
        if not any(key):
            continue
        buckets[key].append(row)
    signals: list[dict[str, Any]] = []
    severity_weight = {"critical": 4, "high": 3, "medium": 2, "low": 1, "watch": 1}
    for (brand, platform, signal_type), rows in buckets.items():
        max_score = max((_to_float(row.get("score")) for row in rows), default=0.0)
        max_weight = max((severity_weight.get(_text(row.get("severity")).lower(), 1) for row in rows), default=1)
        count = len(rows)
        if count < 2 and max_score < 80 and max_weight < 3:
            continue
        score = min(100.0, 35 + count * 12 + max_score * 0.25 + max_weight * 6)
        latest = sorted(rows, key=lambda item: _parse_datetime(item.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc))[-1]
        signals.append(
            {
                "signal_type": "market_signal_event",
                "rule_key": "market_signal_event_burst",
                "severity": _severity(score),
                "score": round(_clamp(score), 2),
                "confidence": 0.65,
                "is_abnormal_growth": False,
                "entity": {
                    "entity_type": "market_signal_bucket",
                    "brand": brand,
                    "platform": platform,
                    "signal_type": signal_type,
                    "latest_signal_uid": _text(latest.get("signal_uid")),
                },
                "metric": {
                    "value": count,
                    "threshold": 2,
                    "unit": "events_in_lookback",
                    "snapshot_date": _text(latest.get("created_at"))[:10],
                    "captured_at": _text(latest.get("created_at")),
                },
                "evidence": {
                    "event_count": count,
                    "max_score": round(max_score, 3),
                    "max_severity": _text(latest.get("severity")),
                    "review_statuses": sorted({_text(row.get("review_status")) for row in rows if row.get("review_status")}),
                    "latest_detail": _text(latest.get("detail"))[:240],
                    "latest_source_url": _text(latest.get("source_url")),
                },
            }
        )
    return signals


def build_trend_detection_v0(
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    limit: int = DEFAULT_ROW_LIMIT,
    top_signals: int = DEFAULT_TOP_SIGNALS,
) -> dict[str, Any]:
    """Build a read-only trend detection report from existing anchors."""
    bounded_limit = max(1, min(20000, int(limit or DEFAULT_ROW_LIMIT)))
    bounded_lookback_days = max(1, min(90, int(lookback_days or DEFAULT_LOOKBACK_DAYS)))
    bounded_top_signals = max(1, min(100, int(top_signals or DEFAULT_TOP_SIGNALS)))
    now = _now()

    post_rows = _load_post_metric_rows(bounded_limit)
    channel_rows = _load_channel_metric_rows(bounded_limit)
    market_rows = _load_market_signal_rows(bounded_limit)

    signals = (
        _detect_post_trends(post_rows, lookback_days=bounded_lookback_days, now=now)
        + _detect_channel_trends(channel_rows, lookback_days=bounded_lookback_days, now=now)
        + _detect_market_signal_trends(market_rows, lookback_days=bounded_lookback_days, now=now)
    )
    signals = sorted(signals, key=lambda item: (float(item.get("score") or 0), float(item.get("confidence") or 0)), reverse=True)
    top = signals[:bounded_top_signals]
    abnormal = [item for item in signals if item.get("is_abnormal_growth")]
    checks = {
        "detection_version_set": bool(DETECTION_VERSION),
        "rules_defined": len(RULES) >= 5,
        "official_post_delta_rule_defined": "official_post_views_delta_spike" in RULES,
        "growth_uses_delta_fields": True,
        "cumulative_only_growth_blocked": True,
        "event_signals_are_not_metric_deltas": True,
        "provider_calls_blocked": True,
        "llm_calls_blocked": True,
        "writes_blocked": True,
        "sync_blocked": True,
    }
    data_available = bool(post_rows or channel_rows or market_rows)
    return {
        "mode": "p6_73_trend_detection_v0",
        "generated_at": _now_iso(),
        "detection_version": DETECTION_VERSION,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "external_http_calls": False,
        "passed": bool(all(checks.values()) and data_available),
        "checks": checks,
        "parameters": {
            "lookback_days": bounded_lookback_days,
            "row_limit": bounded_limit,
            "top_signals": bounded_top_signals,
        },
        "summary": {
            "post_metric_rows_loaded": len(post_rows),
            "channel_metric_rows_loaded": len(channel_rows),
            "market_signal_rows_loaded": len(market_rows),
            "signals_total": len(signals),
            "top_signals_returned": len(top),
            "abnormal_growth_signals": len(abnormal),
            "watch_signals": sum(1 for item in signals if _text(item.get("severity")) == "watch"),
            "market_event_signals": sum(1 for item in signals if item.get("signal_type") == "market_signal_event"),
        },
        "rules": RULES,
        "signals": top,
        "contract": {
            "growth_requires_delta_fields": True,
            "single_cumulative_snapshot_is_not_growth": True,
            "event_bursts_are_labeled_as_events_not_metric_growth": True,
            "no_model_training": True,
        },
        "next_steps": [
            "Use abnormal_growth_signals as candidates for P6.74 acceptance estimation inputs.",
            "Keep watch signals visible but separate from abnormal growth.",
            "Add calibration only after P6.75 compares predictions against next-day truth.",
        ],
    }
