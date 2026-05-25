"""No-write review packages for market-signal promotion candidates."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.domains.market.signal_classifier import VILTROX_TERMS
from app.domains.market.signal_taxonomy import TIER1_GROUPS, TIER2_GROUPS


READY_SIGNAL_TYPES = {
    "competitor_focus",
    "competitor_launch",
    "pricing_sensitive",
    "product_comparison",
    "voc_issue",
}

EXTERNAL_REVIEW_GROUPS = TIER1_GROUPS | TIER2_GROUPS | {"viltrox_products"}


def _now_z() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _text(*values: Any) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _safe_float(value: Any) -> float:
    try:
        parsed = float(value or 0)
        return parsed if parsed == parsed else 0.0
    except Exception:
        return 0.0


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _confidence(signal: dict[str, Any]) -> float:
    evidence = signal.get("evidence") if isinstance(signal.get("evidence"), dict) else {}
    return _safe_float(signal.get("confidence") or evidence.get("confidence"))


def _source_host(url: str) -> str:
    if not url:
        return ""
    try:
        from urllib.parse import urlparse

        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _candidate_key(signal: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        _text(signal.get("signal_uid")),
        _text(signal.get("brand")).lower(),
        _text(signal.get("signal_type")).lower(),
        str(signal.get("source_id") or ""),
    )


def _external_candidate_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        _text(item.get("source_uid")),
        _text(item.get("source_url")).lower(),
        _text(item.get("title")).lower(),
    )


def _review_candidate(signal: dict[str, Any]) -> dict[str, Any]:
    brand = _text(signal.get("normalized_brand"), signal.get("brand")).lower()
    signal_type = _text(signal.get("signal_type"))
    severity = _text(signal.get("severity") or "low").lower()
    score = _safe_float(signal.get("score"))
    confidence = _confidence(signal)
    product_hints = [str(item).lower() for item in _safe_list(signal.get("product_hints")) if str(item).strip()]
    source_table = _text(signal.get("source_table"))
    source_id = signal.get("source_id")
    source_url = _text(signal.get("source_url"))
    detail = _text(signal.get("detail"))
    reasons: list[str] = []

    action = "pending_review"
    if not brand or brand == "unknown":
        reasons.append("missing_brand")
    elif brand in VILTROX_TERMS:
        action = "rejected"
        reasons.append("viltrox_brand_not_competitor")
    elif not source_table or not source_id or not source_url:
        reasons.append("missing_source")
    elif not detail:
        reasons.append("missing_detail")
    elif confidence < 0.55:
        reasons.append("low_classifier_confidence")
    elif signal_type == "competitor_mention" and score < 25 and len(product_hints) <= 1:
        action = "ignored"
        reasons.append("generic_competitor_mention")
    elif severity in {"critical", "danger", "high"}:
        action = "ready"
        reasons.append(f"severity:{severity}")
    elif signal_type in READY_SIGNAL_TYPES and confidence >= 0.6:
        action = "ready"
        reasons.append(f"signal_type:{signal_type}")
    elif score >= 35 and confidence >= 0.6:
        action = "ready"
        reasons.append(f"score:{score:g}")
    else:
        reasons.append("valid_but_needs_manual_review")

    if action == "ready" and product_hints:
        reasons.append("has_product_hints")

    return {
        "signal_uid": _text(signal.get("signal_uid")),
        "brand": brand,
        "normalized_brand": brand,
        "signal_type": signal_type,
        "severity": severity,
        "score": score,
        "confidence": round(confidence, 3),
        "suggested_action": action,
        "reasons": reasons,
        "source_table": source_table,
        "source_id": source_id,
        "source_url": source_url,
        "platform": _text(signal.get("platform")),
        "detail": detail[:500],
        "product_hints": product_hints,
        "review_status": signal.get("review_status") or "pending_review",
        "evidence": signal.get("evidence") if isinstance(signal.get("evidence"), dict) else {},
    }


def build_market_signal_review_package(classifier_payload: dict[str, Any]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for raw in classifier_payload.get("promotion_candidates") or []:
        if not isinstance(raw, dict):
            continue
        key = _candidate_key(raw)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(raw)

    review_items = [_review_candidate(signal) for signal in candidates]
    action_counts = Counter(item["suggested_action"] for item in review_items)
    brand_counts = Counter(item["brand"] for item in review_items if item.get("brand"))
    type_counts = Counter(item["signal_type"] for item in review_items if item.get("signal_type"))
    ready_candidates = [item for item in review_items if item["suggested_action"] == "ready"]
    pending_candidates = [item for item in review_items if item["suggested_action"] == "pending_review"]
    ignored_candidates = [item for item in review_items if item["suggested_action"] == "ignored"]
    rejected_candidates = [item for item in review_items if item["suggested_action"] == "rejected"]

    checks = {
        "source_classifier_dry_run": classifier_payload.get("write_db") is False,
        "source_classifier_passed": bool(classifier_payload.get("passed")),
        "write_db_blocked": True,
        "llm_calls_blocked": True,
        "gemini_calls_blocked": True,
        "no_competitor_signal_write": True,
        "all_candidates_reviewed": len(review_items) == len(candidates),
        "ready_have_sources": all(
            bool(item.get("source_table") and item.get("source_id") and item.get("source_url"))
            for item in ready_candidates
        ),
        "ready_excludes_viltrox": all(str(item.get("brand") or "").lower() not in VILTROX_TERMS for item in ready_candidates),
    }

    return {
        "mode": "market_signal_promotion_review_package_v0",
        "generated_at": _now_z(),
        "write_db": False,
        "llm_calls": False,
        "gemini_calls": False,
        "sync_triggered": False,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "source_classifier": {
            "mode": classifier_payload.get("mode"),
            "generated_at": classifier_payload.get("generated_at"),
            "summary": classifier_payload.get("summary") or {},
        },
        "summary": {
            "candidates_loaded": len(candidates),
            "ready_for_promotion": len(ready_candidates),
            "pending_manual_review": len(pending_candidates),
            "ignored": len(ignored_candidates),
            "rejected": len(rejected_candidates),
            "suggested_action_counts": dict(action_counts),
            "brand_counts": dict(brand_counts),
            "signal_type_counts": dict(type_counts),
            "avg_review_confidence": round(
                sum(float(item.get("confidence") or 0) for item in review_items) / len(review_items),
                3,
            )
            if review_items
            else 0.0,
        },
        "review_items": review_items,
        "ready_candidates": ready_candidates,
        "pending_candidates": pending_candidates,
        "ignored_candidates": ignored_candidates,
        "rejected_candidates": rejected_candidates,
        "policy": {
            "backup_required_before_write": True,
            "promotion_target": "vkpi_competitor_signals",
            "competitor_signals_after_review_only": True,
            "no_llm_fact_generation": True,
            "no_provider_calls": True,
        },
    }


def _external_keyword_hits(item: dict[str, Any]) -> list[str]:
    hits = item.get("keyword_hits")
    if isinstance(hits, list):
        return [str(hit).strip().lower() for hit in hits if str(hit).strip()]
    return []


def _external_group_hits(item: dict[str, Any]) -> dict[str, list[str]]:
    groups = item.get("keyword_groups") if isinstance(item.get("keyword_groups"), dict) else {}
    result: dict[str, list[str]] = {}
    for group, hits in groups.items():
        if not isinstance(hits, list):
            continue
        clean = [str(hit).strip().lower() for hit in hits if str(hit).strip()]
        if clean:
            result[str(group)] = clean
    return result


def _external_review_candidate(item: dict[str, Any], source_report: dict[str, Any]) -> dict[str, Any]:
    groups = _external_group_hits(item)
    group_names = set(groups)
    score = _safe_float(item.get("score"))
    title = _text(item.get("title"))
    summary = _text(item.get("summary"))
    source_url = _text(item.get("source_url"))
    source_uid = _text(item.get("source_uid"))
    provider = _text(item.get("provider"), "external")
    source_key = _text(item.get("source_key"))
    source_group = _text((source_report.get("summary") or {}).get("selected_source_group"))
    keyword_hits = _external_keyword_hits(item)
    primary_groups = sorted(group_names & EXTERNAL_REVIEW_GROUPS)
    reasons: list[str] = []
    suggested_action = "pending_review"
    write_target = "vkpi_market_mentions"
    secondary_target = ""

    if not source_url or not title:
        reasons.append("missing_source_url_or_title")
    elif not keyword_hits:
        suggested_action = "ignored"
        reasons.append("no_taxonomy_keyword_hit")
        write_target = ""
    elif not bool(item.get("business_signal")):
        suggested_action = "ignored"
        reasons.append("keyword_hit_but_not_business_signal")
        write_target = ""
    elif group_names & {"viltrox_products"}:
        suggested_action = "ready"
        reasons.append("viltrox_product_or_brand_signal")
    elif group_names & TIER1_GROUPS and score >= 0.25:
        suggested_action = "ready"
        reasons.append("tier1_competitor_or_ecosystem_signal")
        secondary_target = "vkpi_competitor_signals_after_market_mention"
    elif group_names & TIER2_GROUPS and score >= 0.25:
        suggested_action = "pending_review"
        reasons.append("tier2_signal_needs_human_review")
        secondary_target = "vkpi_competitor_signals_after_market_mention"
    else:
        reasons.append("low_score_or_context")

    if suggested_action == "ready" and not secondary_target and group_names & (TIER1_GROUPS | TIER2_GROUPS):
        secondary_target = "vkpi_competitor_signals_after_market_mention"

    return {
        "source_uid": source_uid,
        "provider": provider,
        "source_key": source_key,
        "source_group": source_group,
        "source_type": _text(item.get("source_type")),
        "source_url": source_url,
        "source_host": _source_host(source_url),
        "title": title[:500],
        "summary": summary[:700],
        "published_at": _text(item.get("published_at")),
        "score": round(score, 3),
        "business_signal": bool(item.get("business_signal")),
        "keyword_hits": keyword_hits,
        "keyword_groups": groups,
        "primary_groups": primary_groups,
        "suggested_action": suggested_action,
        "reasons": reasons,
        "write_target": write_target,
        "secondary_target": secondary_target,
        "write_prerequisite": "persist external item to market mention table first" if write_target else "",
        "review_status": "pending_review" if suggested_action in {"ready", "pending_review"} else suggested_action,
        "evidence": {
            "source_report_mode": source_report.get("mode"),
            "source_report_generated_at": source_report.get("generated_at"),
            "source_report_group": source_group,
            "provider_calls": bool(source_report.get("provider_calls")),
            "write_db": bool(source_report.get("write_db")),
        },
    }


def build_external_signal_review_package(smoke_payloads: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a no-write human review package from Google/RSS external smoke payloads."""

    review_items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    source_reports: list[dict[str, Any]] = []
    for payload in smoke_payloads:
        if not isinstance(payload, dict):
            continue
        source_reports.append(
            {
                "mode": payload.get("mode"),
                "generated_at": payload.get("generated_at"),
                "provider_calls": bool(payload.get("provider_calls")),
                "write_db": bool(payload.get("write_db")),
                "summary": payload.get("summary") or {},
            }
        )
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            key = _external_candidate_key(item)
            if key in seen:
                continue
            seen.add(key)
            review_items.append(_external_review_candidate(item, payload))

    ready_candidates = [item for item in review_items if item["suggested_action"] == "ready"]
    pending_candidates = [item for item in review_items if item["suggested_action"] == "pending_review"]
    ignored_candidates = [item for item in review_items if item["suggested_action"] == "ignored"]
    action_counts = Counter(item["suggested_action"] for item in review_items)
    source_counts = Counter(item["source_key"] for item in review_items if item.get("source_key"))
    group_counts = Counter(group for item in review_items for group in item.get("primary_groups") or [])
    write_target_counts = Counter(item["write_target"] for item in review_items if item.get("write_target"))
    checks = {
        "source_smokes_passed": all(bool(payload.get("passed")) for payload in smoke_payloads if isinstance(payload, dict)),
        "source_smokes_are_no_write": all(payload.get("write_db") is False for payload in smoke_payloads if isinstance(payload, dict)),
        "write_db_blocked": True,
        "llm_calls_blocked": True,
        "gemini_calls_blocked": True,
        "sync_blocked": True,
        "all_candidates_reviewed": len(review_items) == len(seen),
        "ready_have_source_urls": all(bool(item.get("source_url")) for item in ready_candidates),
        "ready_requires_market_mention_first": all(
            item.get("write_target") == "vkpi_market_mentions" and bool(item.get("write_prerequisite"))
            for item in ready_candidates
        ),
    }

    return {
        "mode": "market_external_signal_review_package_v0",
        "generated_at": _now_z(),
        "write_db": False,
        "llm_calls": False,
        "gemini_calls": False,
        "sync_triggered": False,
        "provider_calls": False,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "source_reports": source_reports,
        "summary": {
            "source_report_count": len(source_reports),
            "items_loaded": len(review_items),
            "ready_for_market_mentions": len(ready_candidates),
            "pending_manual_review": len(pending_candidates),
            "ignored": len(ignored_candidates),
            "suggested_action_counts": dict(action_counts),
            "source_counts": dict(source_counts),
            "primary_group_counts": dict(group_counts),
            "write_target_counts": dict(write_target_counts),
            "candidate_competitor_signal_after_market_mention": sum(
                1 for item in review_items if item.get("secondary_target")
            ),
        },
        "review_items": review_items,
        "ready_candidates": ready_candidates,
        "pending_candidates": pending_candidates,
        "ignored_candidates": ignored_candidates,
        "policy": {
            "no_db_write": True,
            "promotion_target_first": "vkpi_market_mentions",
            "secondary_target_after_source_row": "vkpi_competitor_signals",
            "backup_required_before_write": True,
            "human_review_required": True,
            "no_llm_fact_generation": True,
            "no_provider_calls": True,
        },
    }


def build_market_signal_review_package_from_file(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("classifier payload must be a JSON object")
    if payload.get("mode") == "market_signal_promotion_review_package_v0":
        return payload
    return build_market_signal_review_package(payload)


def build_external_signal_review_package_from_files(paths: list[str | Path]) -> dict[str, Any]:
    payloads: list[dict[str, Any]] = []
    for path in paths:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("external smoke payload must be a JSON object")
        payloads.append(payload)
    return build_external_signal_review_package(payloads)


def competitor_signal_rows_from_review_package(package: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in package.get("ready_candidates") or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "signal_uid": _text(item.get("signal_uid")),
                "brand": _text(item.get("brand")),
                "normalized_brand": _text(item.get("normalized_brand"), item.get("brand")).lower(),
                "signal_type": _text(item.get("signal_type")),
                "severity": _text(item.get("severity") or "medium"),
                "score": round(_safe_float(item.get("score")), 3),
                "product_hints_json": json.dumps(item.get("product_hints") or [], ensure_ascii=False),
                "source_table": _text(item.get("source_table")),
                "source_id": item.get("source_id"),
                "source_sheet": "",
                "source_row": None,
                "source_url": _text(item.get("source_url")),
                "platform": _text(item.get("platform")),
                "detail": _text(item.get("detail")),
                "evidence_json": json.dumps(
                    {
                        "review_package": "market_signal_promotion_review_package_v0",
                        "suggested_action": item.get("suggested_action"),
                        "confidence": item.get("confidence"),
                        "reasons": item.get("reasons") or [],
                        "source_evidence": item.get("evidence") or {},
                    },
                    ensure_ascii=False,
                ),
                "review_status": "pending_review",
            }
        )
    return rows
