"""Dry-run mapper from provider smoke reports to V-KPI market signal tables.

This module does not write to the database. It produces a deterministic package
that can be reviewed before the backup-first write slice.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.domains.market.signal_taxonomy import TIER1_GROUPS, TIER2_GROUPS, dedupe_keywords


TARGET_TABLES = ["vkpi_market_scan_runs", "vkpi_market_sources", "vkpi_market_mentions"]


def _now_z() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _text(*values: Any) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _safe_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def _safe_float(value: Any) -> float:
    try:
        parsed = float(value or 0)
        return parsed if parsed == parsed else 0.0
    except Exception:
        return 0.0


def _post_signal_score(post: dict[str, Any]) -> float:
    groups = post.get("keyword_groups") if isinstance(post.get("keyword_groups"), dict) else {}
    tier1_hits = sum(len(groups.get(name, [])) for name in TIER1_GROUPS)
    tier2_hits = sum(len(groups.get(name, [])) for name in TIER2_GROUPS)
    viltrox_hits = len(groups.get("viltrox_products", []))
    comments = min(_safe_int(post.get("num_comments")), 80)
    score = 0.18 + tier1_hits * 0.13 + tier2_hits * 0.08 + viltrox_hits * 0.18 + comments * 0.004
    return round(max(0.0, min(1.0, score)), 3)


def _source_ref(post: dict[str, Any]) -> str:
    return _text(post.get("source_uid"), f"reddit:{post.get('post_id')}" if post.get("post_id") else "")


def _mention_products(post: dict[str, Any]) -> tuple[str, str]:
    groups = post.get("keyword_groups") if isinstance(post.get("keyword_groups"), dict) else {}
    competitors: list[str] = []
    for group_name in sorted(TIER1_GROUPS | TIER2_GROUPS):
        competitors.extend(str(item).lower() for item in groups.get(group_name, []))
    viltrox_products = [str(item).lower() for item in groups.get("viltrox_products", [])]
    return ", ".join(sorted(set(viltrox_products))), ", ".join(sorted(set(competitors)))


def _is_business_signal(post: dict[str, Any]) -> bool:
    groups = post.get("keyword_groups") if isinstance(post.get("keyword_groups"), dict) else {}
    if groups.get("viltrox_products"):
        return True
    return any(groups.get(name) for name in TIER1_GROUPS | TIER2_GROUPS)


def _source_row(post: dict[str, Any]) -> dict[str, Any]:
    source_ref = _source_ref(post)
    return {
        "source_temp_uid": source_ref,
        "source_type": "reddit_post",
        "platform": "reddit",
        "source_ref": source_ref,
        "source_url": _text(post.get("source_url")),
        "title": _text(post.get("title"))[:500],
        "metadata_json": {
            "subreddit": _text(post.get("subreddit")),
            "post_id": _text(post.get("post_id")),
            "author": _text(post.get("author")),
            "score": _safe_int(post.get("score")),
            "num_comments": _safe_int(post.get("num_comments")),
            "keyword_hits": post.get("keyword_hits") or [],
            "keyword_groups": post.get("keyword_groups") or {},
            "raw_payload_hash": _text(post.get("raw_payload_hash")),
            "published_at": _text(post.get("published_at")),
        },
    }


def _mention_row(post: dict[str, Any]) -> dict[str, Any]:
    product_sku, competitor_product = _mention_products(post)
    return {
        "source_temp_uid": _source_ref(post),
        "platform": "reddit",
        "handle": _text(post.get("author")),
        "mention_text": _text(post.get("title"))[:1000],
        "product_sku": product_sku,
        "competitor_product": competitor_product,
        "sentiment": "",
        "score": _post_signal_score(post),
        "metadata_json": {
            "subreddit": _text(post.get("subreddit")),
            "post_id": _text(post.get("post_id")),
            "source_url": _text(post.get("source_url")),
            "keyword_hits": post.get("keyword_hits") or [],
            "keyword_groups": post.get("keyword_groups") or {},
            "raw_payload_hash": _text(post.get("raw_payload_hash")),
            "review_status": "raw_unreviewed",
            "promotion_target": "vkpi_competitor_signals_after_review",
        },
    }


def _candidate_posts(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for result in report.get("subreddit_results") or []:
        if not isinstance(result, dict):
            continue
        for post in result.get("sample_posts") or []:
            if not isinstance(post, dict):
                continue
            ref = _source_ref(post)
            if not ref or ref in seen:
                continue
            if not post.get("keyword_hits"):
                continue
            if not _is_business_signal(post):
                continue
            seen.add(ref)
            rows.append(post)
    for post in report.get("top_signal_candidates") or []:
        if not isinstance(post, dict):
            continue
        ref = _source_ref(post)
        if not ref or ref in seen:
            continue
        if not post.get("keyword_hits"):
            continue
        if not _is_business_signal(post):
            continue
        seen.add(ref)
        rows.append(post)
    return rows


def _external_review_candidates(package: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in package.get("ready_candidates") or []:
        if not isinstance(item, dict):
            continue
        if item.get("write_target") != "vkpi_market_mentions":
            continue
        ref = _text(item.get("source_uid"), item.get("source_url"))
        if not ref or ref in seen:
            continue
        if not _text(item.get("source_url")):
            continue
        seen.add(ref)
        rows.append(item)
    return rows


def _external_source_row(item: dict[str, Any]) -> dict[str, Any]:
    source_ref = _text(item.get("source_uid"), item.get("source_url"))
    provider = _text(item.get("provider"), "external")
    platform = "google_news" if provider == "google_news" else provider
    return {
        "source_temp_uid": source_ref,
        "source_type": _text(item.get("source_type"), "external_signal"),
        "platform": platform,
        "source_ref": source_ref,
        "source_url": _text(item.get("source_url")),
        "title": _text(item.get("title"))[:500],
        "metadata_json": {
            "provider": provider,
            "source_key": _text(item.get("source_key")),
            "source_group": _text(item.get("source_group")),
            "source_host": _text(item.get("source_host")),
            "published_at": _text(item.get("published_at")),
            "score": _safe_float(item.get("score")),
            "keyword_hits": item.get("keyword_hits") or [],
            "keyword_groups": item.get("keyword_groups") or {},
            "primary_groups": item.get("primary_groups") or [],
            "suggested_action": item.get("suggested_action"),
            "reasons": item.get("reasons") or [],
            "secondary_target": item.get("secondary_target") or "",
        },
    }


def _external_mention_row(item: dict[str, Any]) -> dict[str, Any]:
    product_sku, competitor_product = _mention_products(item)
    provider = _text(item.get("provider"), "external")
    platform = "google_news" if provider == "google_news" else provider
    return {
        "source_temp_uid": _text(item.get("source_uid"), item.get("source_url")),
        "platform": platform,
        "handle": _text(item.get("source_host"), item.get("source_key")),
        "mention_text": _text(item.get("title"), item.get("summary"))[:1000],
        "product_sku": product_sku,
        "competitor_product": competitor_product,
        "sentiment": "",
        "score": _safe_float(item.get("score")),
        "metadata_json": {
            "source_url": _text(item.get("source_url")),
            "source_key": _text(item.get("source_key")),
            "source_group": _text(item.get("source_group")),
            "published_at": _text(item.get("published_at")),
            "summary": _text(item.get("summary"))[:1000],
            "keyword_hits": item.get("keyword_hits") or [],
            "keyword_groups": item.get("keyword_groups") or {},
            "primary_groups": item.get("primary_groups") or [],
            "review_status": "raw_unreviewed",
            "promotion_target": item.get("secondary_target") or "",
            "review_package": "market_external_signal_review_package_v0",
            "review_reasons": item.get("reasons") or [],
        },
    }


def build_market_signal_write_package(report: dict[str, Any]) -> dict[str, Any]:
    posts = _candidate_posts(report)
    sources = [_source_row(post) for post in posts]
    mentions = [_mention_row(post) for post in posts]
    source_refs = {row["source_temp_uid"] for row in sources}
    mention_refs = {row["source_temp_uid"] for row in mentions}
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    provider_status = report.get("provider_status") if isinstance(report.get("provider_status"), dict) else {}
    generated_at = _now_z()
    run_uid = f"market-signal-reddit-{generated_at.replace(':', '').replace('-', '')}"
    scan_run = {
        "run_uid": run_uid,
        "scan_type": "reddit_signal_smoke",
        "platforms_json": ["reddit"],
        "keywords_json": dedupe_keywords(),
        "status": "completed" if not report.get("errors") else "partial",
        "provider_status": _text(provider_status.get("provider_status"), "configured"),
        "summary_json": {
            "source_report_mode": report.get("mode"),
            "source_generated_at": report.get("generated_at"),
            "source_total_posts": summary.get("total_posts"),
            "source_keyword_hit_posts": summary.get("keyword_hit_posts"),
            "tier1_mentions": summary.get("tier1_mentions"),
            "tier2_mentions": summary.get("tier2_mentions"),
            "viltrox_product_mentions": summary.get("viltrox_product_mentions"),
            "source_report_write_db": bool(report.get("write_db")),
        },
        "error_message": "; ".join(str(item.get("error") or "") for item in report.get("errors") or [] if item.get("error"))[:1000],
        "completed_at": generated_at,
    }
    checks = {
        "write_db_blocked": True,
        "target_tables_known": TARGET_TABLES == ["vkpi_market_scan_runs", "vkpi_market_sources", "vkpi_market_mentions"],
        "source_refs_unique": len(source_refs) == len(sources),
        "mentions_reference_sources": mention_refs.issubset(source_refs),
        "no_empty_source_url": all(bool(row.get("source_url")) for row in sources),
        "no_auto_competitor_signal_write": True,
        "raw_report_was_read_only": report.get("write_db") is False and report.get("llm_calls") is False,
    }
    return {
        "mode": "market_signal_write_package_v0",
        "generated_at": generated_at,
        "write_db": False,
        "llm_calls": False,
        "gemini_calls": False,
        "sync_triggered": False,
        "source_report": {
            "mode": report.get("mode"),
            "generated_at": report.get("generated_at"),
            "provider_path": (report.get("provider_status") or {}).get("primary_path") if isinstance(report.get("provider_status"), dict) else "",
        },
        "target_tables": TARGET_TABLES,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "summary": {
            "candidate_posts": len(posts),
            "sources_to_insert": len(sources),
            "mentions_to_insert": len(mentions),
            "source_report_posts": summary.get("total_posts", 0),
            "source_report_keyword_hit_posts": summary.get("keyword_hit_posts", 0),
            "avg_signal_score": round(sum(_safe_float(row.get("score")) for row in mentions) / len(mentions), 3) if mentions else 0.0,
        },
        "scan_run": scan_run,
        "sources": sources,
        "mentions": mentions,
        "policy": {
            "backup_required_before_write": True,
            "raw_market_mentions_first": True,
            "competitor_signals_after_review_only": True,
            "no_llm_fact_generation": True,
        },
    }


def build_external_market_signal_write_package(review_package: dict[str, Any]) -> dict[str, Any]:
    """Build a no-write raw market mention package from reviewed external signals."""

    items = _external_review_candidates(review_package)
    sources = [_external_source_row(item) for item in items]
    mentions = [_external_mention_row(item) for item in items]
    source_refs = {row["source_temp_uid"] for row in sources}
    mention_refs = {row["source_temp_uid"] for row in mentions}
    generated_at = _now_z()
    safe_stamp = generated_at.replace(":", "").replace("-", "")
    run_uid = f"market-signal-external-{safe_stamp}"
    providers = sorted({row.get("platform") or "external" for row in sources})
    keywords = sorted({str(hit).lower() for item in items for hit in (item.get("keyword_hits") or []) if str(hit).strip()})
    scan_run = {
        "run_uid": run_uid,
        "scan_type": "external_signal_review",
        "platforms_json": providers,
        "keywords_json": keywords or dedupe_keywords(),
        "status": "completed",
        "provider_status": "reviewed_external_smoke",
        "summary_json": {
            "source_package_mode": review_package.get("mode"),
            "source_generated_at": review_package.get("generated_at"),
            "source_report_count": (review_package.get("summary") or {}).get("source_report_count"),
            "source_items_loaded": (review_package.get("summary") or {}).get("items_loaded"),
            "ready_for_market_mentions": len(items),
            "candidate_competitor_signal_after_market_mention": (review_package.get("summary") or {}).get(
                "candidate_competitor_signal_after_market_mention"
            ),
            "source_report_write_db": bool(review_package.get("write_db")),
        },
        "error_message": "",
        "completed_at": generated_at,
    }
    checks = {
        "write_db_blocked": True,
        "target_tables_known": TARGET_TABLES == ["vkpi_market_scan_runs", "vkpi_market_sources", "vkpi_market_mentions"],
        "source_refs_unique": len(source_refs) == len(sources),
        "mentions_reference_sources": mention_refs.issubset(source_refs),
        "no_empty_source_url": all(bool(row.get("source_url")) for row in sources),
        "ready_candidates_only": len(items) == len(review_package.get("ready_candidates") or []),
        "no_auto_competitor_signal_write": True,
        "raw_report_was_read_only": review_package.get("write_db") is False and review_package.get("llm_calls") is False,
    }
    return {
        "mode": "market_signal_write_package_v0",
        "generated_at": generated_at,
        "write_db": False,
        "llm_calls": False,
        "gemini_calls": False,
        "sync_triggered": False,
        "source_report": {
            "mode": review_package.get("mode"),
            "generated_at": review_package.get("generated_at"),
            "provider_path": "external_signal_review_package",
        },
        "target_tables": TARGET_TABLES,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "summary": {
            "candidate_posts": len(items),
            "sources_to_insert": len(sources),
            "mentions_to_insert": len(mentions),
            "source_report_posts": (review_package.get("summary") or {}).get("items_loaded", 0),
            "source_report_keyword_hit_posts": len(items),
            "avg_signal_score": round(sum(_safe_float(row.get("score")) for row in mentions) / len(mentions), 3) if mentions else 0.0,
        },
        "scan_run": scan_run,
        "sources": sources,
        "mentions": mentions,
        "policy": {
            "backup_required_before_write": True,
            "raw_market_mentions_first": True,
            "competitor_signals_after_review_only": True,
            "no_llm_fact_generation": True,
        },
    }


def build_market_signal_write_package_from_file(path: str | Path) -> dict[str, Any]:
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("market signal report must be a JSON object")
    if report.get("mode") == "market_external_signal_review_package_v0":
        return build_external_market_signal_write_package(report)
    return build_market_signal_write_package(report)
