"""Pure daily signal digest builders."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from typing import Any


DIGEST_VERSION = "today-new-signals-v0.1"
DEFAULT_LOOKBACK_HOURS = 24
DEFAULT_LIMIT = 100
COMMENT_OPPORTUNITY_KEYWORDS = (
    "where",
    "buy",
    "price",
    "expensive",
    "cheap",
    "problem",
    "issue",
    "autofocus",
    "mount",
    "available",
    "release",
    "compare",
    "vs",
    "question",
    "?",
    "怎么买",
    "价格",
    "问题",
    "对比",
    "卡口",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


from app.core.coerce import _text


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value if value is not None else default)
        return parsed if parsed == parsed else default
    except Exception:
        return default


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    else:
        raw = _text(value)
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


def _age_hours(value: Any, now: datetime) -> float | None:
    dt = _parse_datetime(value)
    if not dt:
        return None
    return max(0.0, (now - dt).total_seconds() / 3600.0)


def _signal_time(signal: dict[str, Any]) -> str:
    metric = signal.get("metric") if isinstance(signal.get("metric"), dict) else {}
    return _text(metric.get("captured_at") or metric.get("snapshot_date") or signal.get("created_at"))


def _recent_trend_items(trend_report: dict[str, Any], *, now: datetime, lookback_hours: int, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for signal in trend_report.get("signals") or []:
        age = _age_hours(_signal_time(signal), now)
        if age is None or age > lookback_hours:
            continue
        entity = signal.get("entity") if isinstance(signal.get("entity"), dict) else {}
        metric = signal.get("metric") if isinstance(signal.get("metric"), dict) else {}
        items.append(
            {
                "source": "trend_detection_v0",
                "signal_type": signal.get("signal_type"),
                "rule_key": signal.get("rule_key"),
                "severity": signal.get("severity"),
                "score": signal.get("score"),
                "confidence": signal.get("confidence"),
                "is_abnormal_growth": bool(signal.get("is_abnormal_growth")),
                "platform": entity.get("platform"),
                "account_handle": entity.get("account_handle"),
                "post_uid": entity.get("post_uid"),
                "brand": entity.get("brand"),
                "title": entity.get("title"),
                "metric_value": metric.get("value"),
                "threshold": metric.get("threshold"),
                "captured_at": metric.get("captured_at"),
                "age_hours": round(age, 2),
                "evidence": signal.get("evidence") if isinstance(signal.get("evidence"), dict) else {},
            }
        )
    items.sort(key=lambda item: (_float(item.get("score")), _float(item.get("confidence"))), reverse=True)
    return items[: max(1, min(100, int(limit or DEFAULT_LIMIT)))]


def _recent_market_items(rows: list[dict[str, Any]], *, now: datetime, lookback_hours: int, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in rows:
        age = _age_hours(row.get("created_at"), now)
        if age is None or age > lookback_hours:
            continue
        items.append(
            {
                "source": "vkpi_competitor_signals",
                "signal_uid": row.get("signal_uid"),
                "brand": _text(row.get("normalized_brand") or row.get("brand") or "unknown"),
                "signal_type": row.get("signal_type"),
                "severity": row.get("severity"),
                "score": _float(row.get("score")),
                "platform": row.get("platform"),
                "review_status": row.get("review_status"),
                "source_url": row.get("source_url"),
                "detail": _text(row.get("detail"))[:240],
                "created_at": row.get("created_at"),
                "age_hours": round(age, 2),
            }
        )
    items.sort(key=lambda item: (_float(item.get("score")), _text(item.get("created_at"))), reverse=True)
    return items[: max(1, min(100, int(limit or DEFAULT_LIMIT)))]


def _comment_status(*, cached: int, analyzed: int, opportunities: int, negative: int, hostile: int) -> str:
    if cached <= 0:
        return "no_cached_comments"
    if analyzed <= 0:
        return "cached_without_sentiment"
    if hostile > 0 or negative >= 3:
        return "attention_required"
    if opportunities > 0:
        return "opportunity_detected"
    return "quiet"


def _recent_comment_summary(rows: list[dict[str, Any]], *, now: datetime, lookback_hours: int, limit: int) -> dict[str, Any]:
    recent: list[dict[str, Any]] = []
    platform_counts: Counter[str] = Counter()
    sentiment_counts: Counter[str] = Counter()
    brand_attitude_counts: Counter[str] = Counter()
    opportunity_count = 0
    negative_count = 0
    hostile_count = 0
    analyzed_count = 0
    for row in rows:
        age = _age_hours(row.get("fetched_at") or row.get("created_at"), now)
        if age is None or age > lookback_hours:
            continue
        text = _text(row.get("comment_text"))
        sentiment = _text(row.get("sentiment")).lower()
        brand_attitude = _text(row.get("brand_attitude")).lower()
        platform = _text(row.get("platform") or "unknown")
        is_opportunity = bool(text and any(keyword in text.lower() for keyword in COMMENT_OPPORTUNITY_KEYWORDS))
        is_negative = sentiment == "negative"
        is_hostile = brand_attitude == "hostile"
        if sentiment:
            analyzed_count += 1
            sentiment_counts[sentiment] += 1
        if brand_attitude:
            brand_attitude_counts[brand_attitude] += 1
        if is_opportunity:
            opportunity_count += 1
        if is_negative:
            negative_count += 1
        if is_hostile:
            hostile_count += 1
        platform_counts[platform] += 1
        if len(recent) < limit and (is_opportunity or is_negative or is_hostile):
            recent.append(
                {
                    "source": "vkpi_comments",
                    "comment_id": row.get("id"),
                    "platform": platform,
                    "external_post_id": row.get("external_post_id"),
                    "author": row.get("author_handle"),
                    "sentiment": sentiment,
                    "brand_attitude": brand_attitude,
                    "likes": row.get("likes_count"),
                    "reply_count": row.get("reply_count"),
                    "text": text[:240],
                    "fetched_at": row.get("fetched_at"),
                    "age_hours": round(age, 2),
                    "reason": "hostile" if is_hostile else "negative" if is_negative else "opportunity_keyword",
                }
            )
    cached_count = sum(1 for row in rows if (_age_hours(row.get("fetched_at") or row.get("created_at"), now) or 999999) <= lookback_hours)
    return {
        "status": _comment_status(
            cached=cached_count,
            analyzed=analyzed_count,
            opportunities=opportunity_count,
            negative=negative_count,
            hostile=hostile_count,
        ),
        "contract": {
            "declared": None,
            "cached": cached_count,
            "cap": max(1, min(5000, int(limit or DEFAULT_LIMIT))),
            "status": "cached_window" if cached_count > 0 else "not_cached",
        },
        "counts": {
            "cached_recent_comments": cached_count,
            "analyzed_recent_comments": analyzed_count,
            "opportunities": opportunity_count,
            "negative": negative_count,
            "hostile": hostile_count,
            "sentiment": dict(sentiment_counts),
            "brand_attitude": dict(brand_attitude_counts),
            "platforms": dict(platform_counts),
        },
        "items": recent[: max(1, min(50, int(limit or DEFAULT_LIMIT)))],
    }


def _action_items(
    *,
    trends: list[dict[str, Any]],
    market: list[dict[str, Any]],
    comments: dict[str, Any],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for item in trends:
        if item.get("is_abnormal_growth") and len(actions) < 8:
            actions.append(
                {
                    "priority": "high" if _float(item.get("score")) >= 70 else "medium",
                    "action": "review_growth_post",
                    "reason": f"{item.get('platform')} {item.get('rule_key')} score={item.get('score')}",
                    "entity": {
                        "platform": item.get("platform"),
                        "account_handle": item.get("account_handle"),
                        "post_uid": item.get("post_uid"),
                    },
                }
            )
    for item in market:
        if len(actions) >= 12:
            break
        actions.append(
            {
                "priority": "high" if _text(item.get("severity")).lower() in {"critical", "high"} else "medium",
                "action": "review_market_signal",
                "reason": f"{item.get('brand')} {item.get('signal_type')} score={item.get('score')}",
                "entity": {
                    "brand": item.get("brand"),
                    "platform": item.get("platform"),
                    "signal_uid": item.get("signal_uid"),
                },
            }
        )
    for item in comments.get("items") or []:
        if len(actions) >= 16:
            break
        actions.append(
            {
                "priority": "high" if item.get("reason") in {"hostile", "negative"} else "medium",
                "action": "review_comment_signal",
                "reason": item.get("reason"),
                "entity": {
                    "platform": item.get("platform"),
                    "external_post_id": item.get("external_post_id"),
                    "comment_id": item.get("comment_id"),
                },
            }
        )
    if not actions:
        actions.append(
            {
                "priority": "low",
                "action": "no_action_required",
                "reason": "No 24h abnormal growth, market event, or cached comment anomaly met the current rule threshold.",
                "entity": {},
            }
        )
    return actions


def build_today_new_signals_report(
    *,
    trend_report: dict[str, Any],
    market_rows: list[dict[str, Any]],
    comment_rows: list[dict[str, Any]],
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
    limit: int = DEFAULT_LIMIT,
    now: datetime | None = None,
) -> dict[str, Any]:
    bounded_hours = max(1, min(168, int(lookback_hours or DEFAULT_LOOKBACK_HOURS)))
    bounded_limit = max(1, min(500, int(limit or DEFAULT_LIMIT)))
    resolved_now = now or _now()
    trend_items = _recent_trend_items(trend_report, now=resolved_now, lookback_hours=bounded_hours, limit=bounded_limit)
    market_items = _recent_market_items(market_rows, now=resolved_now, lookback_hours=bounded_hours, limit=bounded_limit)
    comment_summary = _recent_comment_summary(comment_rows, now=resolved_now, lookback_hours=bounded_hours, limit=bounded_limit)
    actions = _action_items(trends=trend_items, market=market_items, comments=comment_summary)
    abnormal_growth = [item for item in trend_items if item.get("is_abnormal_growth")]
    checks = {
        "digest_version_set": bool(DIGEST_VERSION),
        "trend_report_loaded": bool(trend_report),
        "trend_report_passed": bool(trend_report.get("passed")),
        "source_pipeline_available": bool(
            trend_report.get("passed")
            or trend_items
            or market_items
            or comment_summary.get("contract", {}).get("cached", 0) > 0
        ),
        "comment_contract_present": bool(comment_summary.get("contract")),
        "action_items_generated": bool(actions),
        "provider_calls_blocked": True,
        "llm_calls_blocked": True,
        "writes_blocked": True,
        "sync_blocked": True,
    }
    return {
        "mode": "p6_76_today_new_signals_v0",
        "generated_at": _iso(resolved_now),
        "digest_version": DIGEST_VERSION,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "external_http_calls": False,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "parameters": {
            "lookback_hours": bounded_hours,
            "limit": bounded_limit,
        },
        "summary": {
            "trend_signals_24h": len(trend_items),
            "abnormal_growth_24h": len(abnormal_growth),
            "market_events_24h": len(market_items),
            "cached_comments_24h": comment_summary.get("counts", {}).get("cached_recent_comments", 0),
            "comment_opportunities_24h": comment_summary.get("counts", {}).get("opportunities", 0),
            "comment_status": comment_summary.get("status"),
            "action_items": len(actions),
            "source_scope": "existing_db_only",
        },
        "trend_signals": trend_items,
        "market_events": market_items,
        "comment_anomalies": comment_summary,
        "action_items": actions,
        "policy": {
            "read_only": True,
            "no_external_fetch": True,
            "no_provider_or_llm": True,
            "no_task_enqueued": True,
            "comments_are_cached_window_only": True,
        },
        "next_steps": [
            "Use action_items as the daily review queue seed.",
            "Keep comment status visible when cached comments are missing or unanalyzed.",
            "Do not infer platform-wide sentiment from declared-only comment counts.",
        ],
    }
