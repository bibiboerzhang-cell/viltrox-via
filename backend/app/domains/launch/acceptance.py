"""Pure new-launch acceptance estimator."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any


ESTIMATOR_VERSION = "new-launch-acceptance-v0.1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


from app.core.coerce import _text


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value if value is not None else default)
        return parsed if parsed == parsed else default
    except Exception:
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value if value is not None else default))
    except Exception:
        return default


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, value))


def _tier(score: float) -> str:
    if score >= 75:
        return "strong_candidate"
    if score >= 55:
        return "qualified_candidate"
    if score >= 35:
        return "watch_candidate"
    return "low_evidence"


def _risk_penalty(risk_tier: str) -> float:
    normalized = _text(risk_tier).lower()
    if normalized == "high":
        return 14.0
    if normalized == "medium":
        return 7.0
    if normalized == "low":
        return 0.0
    return 4.0


def _platform_momentum(trend_report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = defaultdict(lambda: {"score": 0.0, "signals": 0, "abnormal_growth": 0, "examples": []})
    for signal in trend_report.get("signals") or []:
        entity = signal.get("entity") if isinstance(signal.get("entity"), dict) else {}
        platform = _text(entity.get("platform")).lower()
        if not platform:
            continue
        bucket = buckets[platform]
        weighted = _float(signal.get("score")) * max(0.2, _float(signal.get("confidence"), 0.5))
        bucket["score"] = max(float(bucket["score"]), weighted)
        bucket["signals"] = int(bucket["signals"]) + 1
        if signal.get("is_abnormal_growth"):
            bucket["abnormal_growth"] = int(bucket["abnormal_growth"]) + 1
        if len(bucket["examples"]) < 3:
            bucket["examples"].append(
                {
                    "rule_key": signal.get("rule_key"),
                    "score": signal.get("score"),
                    "confidence": signal.get("confidence"),
                    "entity": {
                        "account_handle": entity.get("account_handle"),
                        "post_uid": entity.get("post_uid"),
                        "brand": entity.get("brand"),
                    },
                }
            )
    return dict(buckets)


def _market_summary(campaign_card: dict[str, Any], trend_report: dict[str, Any]) -> dict[str, Any]:
    market_risk = campaign_card.get("market_risk") if isinstance(campaign_card.get("market_risk"), dict) else {}
    brand_counter: Counter[str] = Counter()
    signal_counter: Counter[str] = Counter()
    for signal in trend_report.get("signals") or []:
        if signal.get("signal_type") != "market_signal_event":
            continue
        entity = signal.get("entity") if isinstance(signal.get("entity"), dict) else {}
        brand = _text(entity.get("brand") or "unknown")
        signal_type = _text(entity.get("signal_type") or "unknown")
        brand_counter[brand] += 1
        signal_counter[signal_type] += 1
    return {
        "risk_tier": market_risk.get("risk_tier", "unknown"),
        "risk_score": market_risk.get("risk_score", 0),
        "top_competitor_brands": market_risk.get("top_competitor_brands", []),
        "related_signals": market_risk.get("related_signals", []),
        "trend_event_brands": [{"brand": brand, "count": count} for brand, count in brand_counter.most_common(8)],
        "trend_event_types": dict(signal_counter),
    }


def _score_candidate(
    candidate: dict[str, Any],
    *,
    platform_momentum: dict[str, dict[str, Any]],
    market_risk_tier: str,
) -> dict[str, Any]:
    platform = _text(candidate.get("platform")).lower()
    fit_score = _float(candidate.get("score"))
    confidence = _float(candidate.get("confidence"))
    momentum = platform_momentum.get(platform, {"score": 0.0, "signals": 0, "abnormal_growth": 0, "examples": []})
    momentum_score = min(22.0, _float(momentum.get("score")) * 0.24)
    abnormal_bonus = min(8.0, _int(momentum.get("abnormal_growth")) * 2.0)
    evidence_bonus = min(8.0, len(candidate.get("evidence") or []) * 1.5)
    confidence_bonus = min(8.0, confidence * 8.0)
    risk_penalty = _risk_penalty(market_risk_tier)
    candidate_risk_flags = list(candidate.get("risk_flags") or [])
    candidate_risk_penalty = 8.0 if candidate_risk_flags else 0.0
    score = _clamp(
        fit_score * 0.58
        + momentum_score
        + abnormal_bonus
        + evidence_bonus
        + confidence_bonus
        - risk_penalty
        - candidate_risk_penalty
    )
    evidence = {
        "kol_product_fit_score": round(fit_score, 2),
        "kol_product_fit_confidence": round(confidence, 3),
        "platform_momentum_score": round(momentum_score, 2),
        "platform_signal_count": _int(momentum.get("signals")),
        "platform_abnormal_growth_count": _int(momentum.get("abnormal_growth")),
        "market_risk_tier": market_risk_tier,
        "market_risk_penalty": risk_penalty,
        "candidate_risk_flags": candidate_risk_flags,
        "candidate_evidence": candidate.get("evidence") or [],
        "momentum_examples": momentum.get("examples") or [],
    }
    return {
        "kol_pool_id": candidate.get("kol_pool_id"),
        "platform": candidate.get("platform"),
        "handle": candidate.get("handle"),
        "display_name": candidate.get("display_name"),
        "followers": candidate.get("followers"),
        "avg_views": candidate.get("avg_views"),
        "acceptance_score": round(score, 2),
        "acceptance_tier": _tier(score),
        "confidence": round(min(1.0, 0.25 + confidence * 0.45 + min(0.25, _int(momentum.get("signals")) * 0.03) + (0.05 if candidate.get("evidence") else 0.0)), 3),
        "evidence": evidence,
    }


def build_new_launch_acceptance_report(
    *,
    campaign_card: dict[str, Any],
    trend_report: dict[str, Any],
    sku: str = "",
    kol_limit: int = 200,
    top_n: int = 12,
    lookback_days: int = 14,
    generated_at: str | None = None,
) -> dict[str, Any]:
    bounded_top = max(1, min(50, int(top_n or 12)))
    momentum = _platform_momentum(trend_report)
    market = _market_summary(campaign_card, trend_report)
    market_risk_tier = _text(market.get("risk_tier") or "unknown")
    candidates = [
        _score_candidate(candidate, platform_momentum=momentum, market_risk_tier=market_risk_tier)
        for candidate in (campaign_card.get("kol_candidates") or [])
    ]
    candidates.sort(key=lambda item: (float(item.get("acceptance_score") or 0), float(item.get("confidence") or 0)), reverse=True)
    top_candidates = candidates[:bounded_top]
    checks = {
        "estimator_version_set": bool(ESTIMATOR_VERSION),
        "campaign_card_loaded": bool(campaign_card),
        "campaign_card_passed": bool(campaign_card.get("passed")),
        "trend_report_loaded": bool(trend_report),
        "trend_report_passed": bool(trend_report.get("passed")),
        "top_candidates_generated": bool(top_candidates),
        "top_candidates_have_evidence": all(bool(item.get("evidence")) for item in top_candidates),
        "market_risk_attached": bool(market),
        "provider_calls_blocked": True,
        "llm_calls_blocked": True,
        "writes_blocked": True,
        "sync_blocked": True,
    }
    product = campaign_card.get("product") if isinstance(campaign_card.get("product"), dict) else {}
    strong_count = sum(1 for item in top_candidates if item.get("acceptance_tier") == "strong_candidate")
    return {
        "mode": "p6_74_new_launch_acceptance_v0",
        "generated_at": generated_at or _now(),
        "estimator_version": ESTIMATOR_VERSION,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "external_http_calls": False,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "parameters": {
            "sku": sku,
            "kol_limit": kol_limit,
            "top_n": bounded_top,
            "lookback_days": lookback_days,
        },
        "summary": {
            "sku": product.get("sku"),
            "candidate_count": len(top_candidates),
            "strong_candidate_count": strong_count,
            "market_risk_tier": market_risk_tier,
            "trend_signals_used": trend_report.get("summary", {}).get("signals_total", 0),
            "abnormal_growth_signals_used": trend_report.get("summary", {}).get("abnormal_growth_signals", 0),
            "source_scope": "existing_db_only",
        },
        "product": product,
        "market": market,
        "platform_momentum": momentum,
        "top_candidates": top_candidates,
        "policy": {
            "read_only": True,
            "not_a_trained_model": True,
            "human_approval_required": True,
            "no_project_created": True,
            "no_outreach_triggered": True,
        },
        "next_steps": [
            "Use these candidates as a planning shortlist, not an automatic decision.",
            "P6.75 must compare this estimate with next-day observed truth before calibration.",
            "Keep risk and evidence visible before any recommendation layer consumes the score.",
        ],
    }
