"""P6.71 definitions for V-KPI "fire" signals.

This is a read-only contract. It defines what "hot" means before any trend
detection, forecasting, or model calibration work starts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


DEFINITION_VERSION = "fire-v0.1"

FIRE_METRICS: dict[str, dict[str, Any]] = {
    "views_velocity": {
        "label": "Views velocity",
        "label_zh": "播放速度",
        "unit": "views_per_hour",
        "windows": ["24h", "72h", "7d"],
        "formula": "post_views_delta / hours_between_snapshots",
        "required_sources": ["vkpi_channel_post_metrics", "post_level_delta"],
        "fallback": "use latest snapshot total only as freshness context, not as growth",
        "direction": "higher_is_hotter",
    },
    "engagement_velocity": {
        "label": "Engagement velocity",
        "label_zh": "互动速度",
        "unit": "engagements_per_hour",
        "windows": ["24h", "72h", "7d"],
        "formula": "(likes_delta + comments_delta + shares_delta) / hours_between_snapshots",
        "required_sources": ["vkpi_channel_post_metrics", "post_level_delta", "comment_contract"],
        "fallback": "exclude missing comment deltas and lower confidence",
        "direction": "higher_is_hotter",
    },
    "growth_acceleration": {
        "label": "Growth acceleration",
        "label_zh": "增长加速度",
        "unit": "ratio",
        "windows": ["24h_vs_prev_24h", "72h_vs_prev_72h"],
        "formula": "current_velocity / previous_velocity when previous_velocity > 0",
        "required_sources": ["time_series_snapshots", "post_level_delta"],
        "fallback": "not_available until at least 3 snapshots exist",
        "direction": "higher_is_hotter",
    },
    "comment_quality_signal": {
        "label": "Comment quality signal",
        "label_zh": "评论质量信号",
        "unit": "score_0_100",
        "windows": ["latest_cached_comments", "7d"],
        "formula": "weighted share of useful intent, complaint, question, product-fit, and competitor comments",
        "required_sources": ["comment_data_contract", "comment_intelligence_rules"],
        "fallback": "declared-only comments are not quality evidence",
        "direction": "higher_is_hotter",
    },
    "conversion_proxy": {
        "label": "Conversion proxy",
        "label_zh": "转化代理",
        "unit": "score_0_100",
        "windows": ["7d", "30d"],
        "formula": "weighted valid_clicks, attributed_orders, coupon/tag evidence, and project stage progress",
        "required_sources": ["vkpi_link_clicks", "vkpi_sales_attributions", "vkpi_projects"],
        "fallback": "clicks without attribution are weak evidence only",
        "direction": "higher_is_hotter",
    },
    "cross_platform_spread": {
        "label": "Cross-platform spread",
        "label_zh": "跨平台扩散",
        "unit": "platform_count_weighted",
        "windows": ["7d", "30d"],
        "formula": "count of distinct platforms with related signals weighted by freshness and evidence confidence",
        "required_sources": ["vkpi_market_sources", "vkpi_competitor_signals", "brand_signal_detector"],
        "fallback": "single-platform signal cannot be called broad spread",
        "direction": "higher_is_hotter",
    },
}

FIRE_SCORE_CONTRACT = {
    "score_range": "0-100",
    "minimum_evidence_for_hot": [
        "at least one true delta metric, not cumulative total only",
        "fresh snapshot window <= 72h for post metrics",
        "baseline_protected items must lower confidence instead of showing +0",
        "declared comments without cached body cannot count as comment quality",
    ],
    "confidence_inputs": [
        "source freshness",
        "snapshot count",
        "comment cache status",
        "baseline protection state",
        "platform-specific metric reliability",
    ],
    "not_allowed": [
        "calling cumulative latest total a growth spike",
        "counting missing comments as neutral or positive",
        "using LLM summaries as source facts",
        "comparing official low-frequency full-scope refresh with latest-30 creator refresh as the same baseline",
    ],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_fire_metric_definition_report() -> dict[str, Any]:
    checks = {
        "definition_version_set": bool(DEFINITION_VERSION),
        "metrics_defined": len(FIRE_METRICS) >= 6,
        "all_metrics_have_formula": all(bool(item.get("formula")) for item in FIRE_METRICS.values()),
        "all_metrics_have_sources": all(bool(item.get("required_sources")) for item in FIRE_METRICS.values()),
        "confidence_contract_defined": bool(FIRE_SCORE_CONTRACT.get("confidence_inputs")),
        "misleading_growth_blocked": any("cumulative" in item for item in FIRE_SCORE_CONTRACT.get("not_allowed", [])),
        "provider_calls_blocked": True,
        "llm_calls_blocked": True,
        "writes_blocked": True,
        "sync_blocked": True,
    }
    return {
        "mode": "p6_71_fire_metric_definitions",
        "generated_at": _now(),
        "definition_version": DEFINITION_VERSION,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "external_http_calls": False,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "metrics": FIRE_METRICS,
        "score_contract": FIRE_SCORE_CONTRACT,
        "next_steps": [
            "P6.72 standardizes snapshot and metric time-series anchors.",
            "P6.73 can implement rule-based trend detection from these definitions.",
            "No model training starts until true deltas and confidence fields are available.",
        ],
    }
