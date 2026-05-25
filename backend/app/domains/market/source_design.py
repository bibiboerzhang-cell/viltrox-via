"""Market signal source design domain.

This module defines source contracts and readiness gates. It does not crawl
external sources, call providers, call LLMs, enqueue sync, or write databases.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any


SOURCE_REGISTRY: list[dict[str, Any]] = [
    {
        "source_key": "reddit_community",
        "label": "Reddit community posts",
        "platform": "reddit",
        "source_type": "community_discussion",
        "recommended_path": "oauth_or_best_effort_public_json_then_apify_fallback",
        "current_phase": "design_only",
        "execution_gate": "P5.67_reddit_stability_strategy",
        "env_keys": ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT", "APIFY_TOKEN"],
        "cost_risk": "low_to_medium",
        "legal_operational_risk": "medium",
        "default_limit": 25,
        "refresh_policy": "keyword/subreddit watchlist only; no broad search until review",
        "signal_types": ["voc_issue", "product_question", "competitor_mention", "purchase_intent"],
        "required_fields": ["source_uid", "platform", "source_url", "title", "body", "author", "published_at", "score", "comments_count"],
        "write_targets": ["vkpi_market_sources", "vkpi_market_mentions", "vkpi_competitor_signals_after_review"],
    },
    {
        "source_key": "x_public_posts",
        "label": "X public posts/comments",
        "platform": "x",
        "source_type": "social_post",
        "recommended_path": "apify_or_official_api_when_budget_and_terms_are_approved",
        "current_phase": "design_only",
        "execution_gate": "P5.68_x_comments_go_no_go",
        "env_keys": ["X_BEARER_TOKEN", "APIFY_TOKEN", "APIFY_X_ACTOR_ID", "APIFY_X_COMMENTS_ACTOR_ID"],
        "cost_risk": "medium_to_high",
        "legal_operational_risk": "high",
        "default_limit": 14,
        "refresh_policy": "explicit validation set only; no daily collection until go/no-go",
        "signal_types": ["competitor_mention", "launch_reaction", "complaint", "creator_conversation"],
        "required_fields": ["source_uid", "platform", "source_url", "author", "text", "published_at", "reply_count", "like_count"],
        "write_targets": ["vkpi_market_sources", "vkpi_market_mentions", "vkpi_competitor_signals_after_review"],
    },
    {
        "source_key": "rss_industry_news",
        "label": "Industry RSS/news feeds",
        "platform": "website",
        "source_type": "rss_article",
        "recommended_path": "allowlisted_rss_fetcher",
        "current_phase": "design_only",
        "execution_gate": "P5.69_market_intelligence_v0",
        "env_keys": [],
        "cost_risk": "low",
        "legal_operational_risk": "low_to_medium",
        "default_limit": 20,
        "refresh_policy": "allowlisted domains only; cache feed etag/last_modified",
        "signal_types": ["competitor_launch", "industry_trend", "pricing_news", "review_roundup"],
        "required_fields": ["source_uid", "source_url", "domain", "title", "summary", "published_at", "canonical_url"],
        "write_targets": ["vkpi_market_sources", "vkpi_market_mentions", "vkpi_competitor_signals_after_review"],
    },
    {
        "source_key": "competitor_site_watch",
        "label": "Competitor official sites",
        "platform": "website",
        "source_type": "competitor_site",
        "recommended_path": "allowlisted_pages_with_hash_diff",
        "current_phase": "design_only",
        "execution_gate": "P5.69_market_intelligence_v0",
        "env_keys": [],
        "cost_risk": "low",
        "legal_operational_risk": "medium",
        "default_limit": 10,
        "refresh_policy": "allowlisted product/news pages only; robots/terms review before fetch",
        "signal_types": ["competitor_launch", "price_change", "spec_change"],
        "required_fields": ["source_uid", "source_url", "brand", "title", "page_hash", "observed_at", "change_type"],
        "write_targets": ["vkpi_market_sources", "vkpi_competitor_signals_after_review"],
    },
    {
        "source_key": "youtube_review_watch",
        "label": "YouTube review/search watch",
        "platform": "youtube",
        "source_type": "video_search",
        "recommended_path": "youtube_api_first_apify_quota_fallback",
        "current_phase": "design_only",
        "execution_gate": "market_v0_after_data_trust",
        "env_keys": ["YOUTUBE_API_KEY", "GOOGLE_YOUTUBE_API_KEY", "APIFY_TOKEN", "APIFY_YOUTUBE_ACTOR_ID"],
        "cost_risk": "medium",
        "legal_operational_risk": "medium",
        "default_limit": 25,
        "refresh_policy": "campaign keywords and known competitors only",
        "signal_types": ["review_video", "competitor_comparison", "creator_opportunity"],
        "required_fields": ["source_uid", "platform", "source_url", "channel", "title", "published_at", "views", "comments_count"],
        "write_targets": ["vkpi_market_sources", "vkpi_market_mentions", "vkpi_competitor_signals_after_review"],
    },
]


CANONICAL_CONTRACT = {
    "identity": ["source_uid", "source_type", "platform", "source_url"],
    "provenance": ["provider", "provider_run_id", "captured_at", "raw_payload_hash", "terms_gate"],
    "content": ["title", "text", "author_or_channel", "published_at", "language"],
    "metrics": ["views", "likes", "comments_count", "score"],
    "classification": ["signal_type", "brand", "product_hint", "sentiment", "confidence"],
    "review": ["review_status", "reviewed_by", "reviewed_at", "decision_note"],
}
TABLE_NAMES = (
    "vkpi_market_scan_runs",
    "vkpi_market_sources",
    "vkpi_market_mentions",
    "vkpi_competitor_signal_runs",
    "vkpi_competitor_signals",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _env_status(names: list[str]) -> dict[str, bool]:
    return {name: bool(os.environ.get(name, "").strip()) for name in names}


def source_readiness(source: dict[str, Any]) -> dict[str, Any]:
    env = _env_status(list(source.get("env_keys") or []))
    configured_keys = [key for key, configured in env.items() if configured]
    required_fields = list(source.get("required_fields") or [])
    missing_contract_fields = [field for field in ("source_uid", "source_url") if field not in required_fields]
    return {
        **source,
        "external_calls_allowed": False,
        "write_db_allowed": False,
        "provider_configured": bool(configured_keys) if env else True,
        "configured_env_keys": configured_keys,
        "env_status": env,
        "contract_complete": not missing_contract_fields,
        "missing_contract_fields": missing_contract_fields,
        "can_collect_now": False,
        "block_reason": "design_phase_only_external_collection_disabled",
    }


def build_market_source_design_report_from_tables(
    tables: dict[str, dict[str, Any]],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    sources = [source_readiness(source) for source in SOURCE_REGISTRY]
    high_risk_blocked = all(
        source["external_calls_allowed"] is False
        for source in sources
        if source.get("legal_operational_risk") in {"high", "medium"}
    )
    checks = {
        "market_scan_tables_present": all(tables[name]["exists"] for name in ("vkpi_market_scan_runs", "vkpi_market_sources", "vkpi_market_mentions")),
        "competitor_signal_tables_present": all(tables[name]["exists"] for name in ("vkpi_competitor_signal_runs", "vkpi_competitor_signals")),
        "canonical_contract_defined": all(bool(fields) for fields in CANONICAL_CONTRACT.values()),
        "sources_have_required_contract": all(bool(source.get("contract_complete")) for source in sources),
        "external_calls_blocked": all(source["external_calls_allowed"] is False for source in sources),
        "writes_blocked": all(source["write_db_allowed"] is False for source in sources),
        "high_risk_sources_gated": high_risk_blocked,
        "reddit_has_separate_gate": any(source["source_key"] == "reddit_community" and source["execution_gate"].startswith("P5.67") for source in sources),
        "x_has_separate_gate": any(source["source_key"] == "x_public_posts" and source["execution_gate"].startswith("P5.68") for source in sources),
    }
    return {
        "mode": "p5_66_market_signal_source_design",
        "generated_at": generated_at or _now(),
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "external_http_calls": False,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "summary": {
            "source_count": len(sources),
            "design_only_sources": sum(1 for source in sources if source.get("current_phase") == "design_only"),
            "blocked_sources": sum(1 for source in sources if not source.get("can_collect_now")),
            "tables_present": sum(1 for table in tables.values() if table.get("exists")),
            "existing_market_rows": sum(int(table.get("rows") or 0) for table in tables.values()),
        },
        "canonical_contract": CANONICAL_CONTRACT,
        "tables": tables,
        "sources": sources,
        "next_gates": [
            {"phase": "P5.67", "task": "Reddit stable strategy", "decision": "oauth_or_best_effort; no all-Reddit promise"},
            {"phase": "P5.68", "task": "X comments go/no-go", "decision": "14 validation targets only before any continuation"},
            {"phase": "P5.69", "task": "Market intelligence v0", "decision": "allowlisted RSS/site sources only"},
        ],
    }
