"""Deterministic, evidence-bounded reasons for prospective KOL selection.

The score answers ordering; this contract answers the operator's different
question: why was this creator found, what makes them worth reviewing, what is
still missing, and what should be fetched next.  It never calls an LLM and
never converts a descriptive proxy into a conversion or outreach claim.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


RATIONALE_SCHEMA = "prospective_candidate_rationale_v1"
CLAIM_STATUS = "descriptive_only"


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _score(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return round(parsed, 6) if 0 <= parsed <= 100 else None


def _terms(value: Any, *, limit: int = 6) -> list[str]:
    raw = value if isinstance(value, (list, tuple, set)) else []
    output: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = _text(item)[:120]
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            output.append(text)
        if len(output) >= limit:
            break
    return output


def _card(
    code: str,
    label: str,
    *,
    status: str,
    summary: str,
    score: Any = None,
    evidence_terms: Any = None,
    evidence_fields: Any = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "status": status,
        "summary": _text(summary)[:360],
        "score": _score(score),
        "evidence_terms": _terms(evidence_terms),
        "evidence_fields": _terms(evidence_fields, limit=8),
    }


def build_candidate_selection_rationale(
    *,
    evidence_contract: Mapping[str, Any],
    activation_gate: Mapping[str, Any],
    audience_contract: Mapping[str, Any],
    content_contract: Mapping[str, Any],
    product_use_fit: Any,
    market_activation: Any,
    audience_fit: Any,
    content_execution: Any,
    evidence_confidence: Any,
) -> dict[str, Any]:
    """Return a stable reason/missing-action bundle from already-audited facts."""

    required_product = _terms(evidence_contract.get("required_product_terms"))
    required_scene = _terms(evidence_contract.get("required_scene_terms"))
    matched_product = _terms(evidence_contract.get("matched_product_terms"))
    matched_scene = _terms(evidence_contract.get("matched_scene_terms"))
    product_pass = evidence_contract.get("passed") is True
    activation_pass = activation_gate.get("passed") is True
    sample_count = activation_gate.get("sample_count")
    minimum_samples = activation_gate.get("minimum_sample_count") or 3
    activation_status = _text(activation_gate.get("status")) or "market_activation_missing"

    purpose_product = "、".join(required_product) or "目标产品能力"
    purpose_scene = "、".join(required_scene) or "目标使用场景"
    purpose = f"寻找可能在{purpose_scene}中需要{purpose_product}，并能持续影响目标受众的创作者。"

    if product_pass:
        product_summary = (
            f"公开内容同时支持产品用途（{'、'.join(matched_product)}）"
            f"和使用场景（{'、'.join(matched_scene)}）。"
        )
    else:
        missing_groups = _terms(evidence_contract.get("missing_groups"))
        product_summary = f"尚缺产品用途或场景的双重证据：{'、'.join(missing_groups) or '待补正文/字幕/视觉证据'}。"

    if activation_pass:
        activation_summary = f"已有 {sample_count} 条近期样本，至少一项可解释的观看或互动信号达到严格门槛。"
    elif activation_status == "insufficient_sample":
        activation_summary = f"目前仅有 {sample_count or 0} 条近期样本；严格判断至少需要 {minimum_samples} 条。"
    elif activation_status == "insufficient_metric_sample":
        activation_summary = (
            f"已返回 {sample_count or 0} 条近期样本，但达到门槛的指标有效观测不足 "
            f"{minimum_samples} 条，仍需补证。"
        )
    elif activation_status == "below_floor":
        activation_summary = f"已有 {sample_count or 0} 条样本，但观看/互动信号尚未达到严格门槛。"
    else:
        activation_summary = "缺少可审计的近期视频聚合，暂不能判断市场推进能力。"

    audience_score = _score(audience_fit)
    target_markets = _terms(audience_contract.get("target_markets"))
    target_languages = _terms(audience_contract.get("target_languages"))
    audience_target = " / ".join(filter(None, ["、".join(target_markets), "、".join(target_languages)]))
    audience_summary = (
        f"目标受众适配分 {audience_score:g}" + (f"，目标为 {audience_target}。" if audience_target else "。")
        if audience_score is not None
        else f"缺少受众市场/语言分布{f'（目标 {audience_target}）' if audience_target else ''}。"
    )
    content_score = _score(content_execution)
    content_summary = (
        f"近期内容执行分 {content_score:g}，可供人工复核制作与持续产出能力。"
        if content_score is not None
        else "缺少近期内容制作、稳定更新或原创度证据。"
    )

    cards = [
        _card(
            "product_use_and_scene",
            "产品使用需求与场景",
            status="observed" if product_pass else "pending",
            summary=product_summary,
            score=product_use_fit,
            evidence_terms=[*matched_product, *matched_scene],
            evidence_fields=evidence_contract.get("matched_fields"),
        ),
        _card(
            "market_activation",
            "市场推进能力",
            status="observed" if activation_pass else "pending",
            summary=activation_summary,
            score=market_activation,
            evidence_fields=list((activation_gate.get("observed_metrics") or {}).keys()),
        ),
        _card(
            "audience_fit",
            "受众匹配",
            status="observed" if audience_score is not None else "pending",
            summary=audience_summary,
            score=audience_score,
            evidence_terms=[*target_markets, *target_languages],
        ),
        _card(
            "content_execution",
            "内容执行",
            status="observed" if content_score is not None else "pending",
            summary=content_summary,
            score=content_score,
            evidence_fields=[
                key for key in ("direct_content_execution", "production_quality", "posting_consistency", "originality")
                if key not in set(content_contract.get("missing_signals") or [])
            ],
        ),
    ]

    missing: list[dict[str, Any]] = []
    if not product_pass:
        missing.append({
            "code": "product_scene_evidence",
            "label": "补抓标题之外的正文、字幕或视觉分析，核实产品用途与场景。",
            "next_action": "fetch_content_body_caption_transcript_visual",
            "blocks_strict_qualification": True,
        })
    if not activation_pass and activation_status != "below_floor":
        missing.append({
            "code": activation_status,
            "label": f"补齐近 45 天至少 {minimum_samples} 条视频及观看、点赞、评论数据。",
            "next_action": "fetch_recent_3_5_video_metrics",
            "blocks_strict_qualification": True,
        })
    if audience_score is None:
        missing.append({
            "code": "audience_fit_missing",
            "label": "补充受众市场与语言分布，避免用创作者所在地代替受众。",
            "next_action": "fetch_audience_market_language_distribution",
            "blocks_strict_qualification": False,
        })
    if content_score is None:
        missing.append({
            "code": "content_execution_missing",
            "label": "分析近 3–5 条内容的制作质量、更新稳定性与原创度。",
            "next_action": "analyze_recent_content_execution",
            "blocks_strict_qualification": False,
        })

    strict_ready = product_pass and activation_pass
    decision_readiness = (
        "decision_support_ready"
        if strict_ready and audience_score is not None and content_score is not None
        else "strict_gate_passed_needs_review"
        if strict_ready
        else "pending_evidence"
    )
    next_action = (
        {
            "code": "deprioritize_below_activation_floor",
            "label": "样本已足够但观看/互动未达严格门槛；不进入严格名单，业务例外时再人工复核。",
        }
        if activation_status == "below_floor"
        else
        {
            "code": missing[0]["next_action"],
            "label": missing[0]["label"],
        }
        if missing
        else {
            "code": "human_review_before_outreach",
            "label": "人工复核受众、品牌安全与合作目标后，再决定是否联系。",
        }
    )
    metric_counts = activation_gate.get("metric_sample_counts")
    metric_sufficient = activation_gate.get("metric_sample_sufficient")
    floor_results = activation_gate.get("floor_results")
    activation_evidence = {
        "status": activation_status,
        "sample_count": sample_count,
        "minimum_sample_count": minimum_samples,
        "metric_sample_counts": dict(metric_counts) if isinstance(metric_counts, Mapping) else {},
        "metric_sample_sufficient": (
            dict(metric_sufficient) if isinstance(metric_sufficient, Mapping) else {}
        ),
        "floor_results": dict(floor_results) if isinstance(floor_results, Mapping) else {},
        "claim_status": CLAIM_STATUS,
    }
    return {
        "schema": RATIONALE_SCHEMA,
        "objective": "prospective_growth",
        "purpose": purpose,
        "decision_readiness": decision_readiness,
        "strict_gate_status": "passed" if strict_ready else "blocked",
        "why_find_this_creator": [
            card["summary"] for card in cards if card["status"] == "observed"
        ][:4],
        "reason_cards": cards,
        "activation_evidence": activation_evidence,
        "missing_evidence": missing,
        "next_action": next_action,
        "evidence_confidence": _score(evidence_confidence),
        "claim_status": CLAIM_STATUS,
        "conversion_claim": False,
        "outreach_decision": False,
    }


__all__ = [
    "CLAIM_STATUS",
    "RATIONALE_SCHEMA",
    "build_candidate_selection_rationale",
]
