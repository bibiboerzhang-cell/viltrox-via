"""Pure market intelligence v0 report builders.

This module does not read databases, call providers, call LLMs, enqueue tasks,
or write files. Service facades pass already-loaded competitor signal rows and
table readiness metadata into these builders.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any


LAUNCH_TYPES = {"competitor_launch", "launch_reaction", "product_launch", "product_comparison", "competitor_focus"}
COMMENT_OPPORTUNITY_TYPES = {"voc_issue", "product_question", "complaint", "pricing_sensitive", "risk_watch"}
LAUNCH_PATTERN = re.compile(r"\b(new|launch|released|announc|preorder|pre-order|prototype|rumor)\b", re.I)
COMMENT_PATTERN = re.compile(r"\b(comment|complaint|problem|issue|question|wish|request|price|expensive|cheap)\b", re.I)
MARKET_STORAGE_TABLES = ("vkpi_market_scan_runs", "vkpi_market_sources", "vkpi_market_mentions")
COMPETITOR_SIGNAL_TABLE = "vkpi_competitor_signals"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _score(value: Any) -> float:
    try:
        parsed = float(value or 0)
        return parsed if parsed == parsed else 0.0
    except Exception:
        return 0.0


def _signal_excerpt(signal: dict[str, Any], limit: int = 220) -> str:
    evidence = signal.get("evidence") if isinstance(signal.get("evidence"), dict) else {}
    return _text(evidence.get("detail") or signal.get("detail"))[:limit]


def _normalize_signal(signal: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": signal.get("id"),
        "signal_uid": signal.get("signal_uid"),
        "brand": _text(signal.get("normalized_brand") or signal.get("brand") or "unknown"),
        "signal_type": _text(signal.get("signal_type") or "unknown"),
        "severity": _text(signal.get("severity") or "medium"),
        "score": _score(signal.get("score")),
        "review_status": _text(signal.get("review_status") or "pending_review"),
        "platform": _text(signal.get("platform") or "unknown"),
        "source_url": _text(signal.get("source_url")),
        "source_table": _text(signal.get("source_table")),
        "source_id": signal.get("source_id"),
        "product_hints": signal.get("product_hints") if isinstance(signal.get("product_hints"), list) else [],
        "detail": _signal_excerpt(signal),
    }


def _is_launch_signal(signal: dict[str, Any]) -> bool:
    signal_type = _text(signal.get("signal_type"))
    detail = _signal_excerpt(signal)
    products = signal.get("product_hints") if isinstance(signal.get("product_hints"), list) else []
    return signal_type in LAUNCH_TYPES or bool(products and LAUNCH_PATTERN.search(detail))


def _is_comment_opportunity(signal: dict[str, Any]) -> bool:
    signal_type = _text(signal.get("signal_type"))
    detail = _signal_excerpt(signal)
    return signal_type in COMMENT_OPPORTUNITY_TYPES or bool(COMMENT_PATTERN.search(detail))


def _table_ready(tables: dict[str, Any], table_name: str) -> bool:
    table = tables.get(table_name) if isinstance(tables, dict) else None
    return bool(table.get("exists")) if isinstance(table, dict) else False


def build_market_intelligence_report(
    rows: list[dict[str, Any]],
    *,
    tables: dict[str, Any],
    run_id: int | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    normalized = [_normalize_signal(row) for row in rows]
    brand_counts: Counter[str] = Counter()
    type_counts: Counter[str] = Counter()
    review_counts: Counter[str] = Counter()
    platform_counts: Counter[str] = Counter()
    brand_score: defaultdict[str, float] = defaultdict(float)
    for item in normalized:
        brand = _text(item.get("brand") or "unknown")
        signal_type = _text(item.get("signal_type") or "unknown")
        review_status = _text(item.get("review_status") or "pending_review")
        platform = _text(item.get("platform") or "unknown")
        score = _score(item.get("score"))
        brand_counts[brand] += 1
        type_counts[signal_type] += 1
        review_counts[review_status] += 1
        platform_counts[platform] += 1
        brand_score[brand] += score

    launch_candidates = [_normalize_signal(row) for row in rows if _is_launch_signal(row)]
    comment_opportunities = [_normalize_signal(row) for row in rows if _is_comment_opportunity(row)]
    high_priority = [
        item for item in normalized
        if item["review_status"] in {"pending_review", "ready"} and item["severity"] in {"critical", "high", "medium"}
    ]
    hot_brands = [
        {"brand": brand, "count": count, "score": round(float(brand_score[brand]), 3)}
        for brand, count in brand_counts.most_common(10)
    ]
    hot_topics = [
        {"signal_type": signal_type, "count": count}
        for signal_type, count in type_counts.most_common(10)
    ]
    checks = {
        "market_storage_ready": all(_table_ready(tables, name) for name in MARKET_STORAGE_TABLES),
        "competitor_signals_ready": _table_ready(tables, COMPETITOR_SIGNAL_TABLE),
        "uses_existing_review_store_only": True,
        "external_calls_blocked": True,
        "provider_calls_blocked": True,
        "writes_blocked": True,
        "sync_blocked": True,
        "dashboard_payload_ready": True,
    }
    return {
        "mode": "p5_69_market_intelligence_v0",
        "generated_at": generated_at or _now(),
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "external_http_calls": False,
        "passed": all(bool(value) for value in checks.values()),
        "status": "ready" if normalized else "no_signals",
        "checks": checks,
        "summary": {
            "signals_loaded": len(normalized),
            "launch_candidates": len(launch_candidates),
            "comment_opportunities": len(comment_opportunities),
            "high_priority": len(high_priority),
            "source": COMPETITOR_SIGNAL_TABLE,
            "run_id": run_id,
        },
        "distributions": {
            "brands": dict(brand_counts),
            "signal_types": dict(type_counts),
            "review_status": dict(review_counts),
            "platforms": dict(platform_counts),
        },
        "hot_brands": hot_brands,
        "hot_topics": hot_topics,
        "launch_candidates": sorted(launch_candidates, key=lambda item: (-_score(item.get("score")), item.get("brand") or ""))[:12],
        "comment_opportunities": sorted(comment_opportunities, key=lambda item: (-_score(item.get("score")), item.get("brand") or ""))[:12],
        "high_priority": sorted(high_priority, key=lambda item: (-_score(item.get("score")), item.get("brand") or ""))[:12],
        "tables": tables,
        "policy": {
            "source_scope": "existing_competitor_signals_only",
            "no_external_fetch": True,
            "no_unreviewed_auto_recommendation": True,
            "rss_and_site_sources_remain_design_gated": True,
            "x_and_reddit_collection_remain_separately_gated": True,
        },
    }
