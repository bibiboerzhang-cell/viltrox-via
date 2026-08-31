"""Pure reason-card builders for prospective candidate selection."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


def _product_context(
    evidence_contract: Mapping[str, Any],
    *,
    terms: Callable[..., list[str]],
) -> tuple[list[str], list[str], bool, str, str]:
    required_product = terms(evidence_contract.get("required_product_terms"))
    required_scene = terms(evidence_contract.get("required_scene_terms"))
    matched_product = terms(evidence_contract.get("matched_product_terms"))
    matched_scene = terms(evidence_contract.get("matched_scene_terms"))
    product_pass = evidence_contract.get("passed") is True
    purpose_product = "、".join(required_product) or "目标产品能力"
    purpose_scene = "、".join(required_scene) or "目标使用场景"
    purpose = f"寻找可能在{purpose_scene}中需要{purpose_product}，并能持续影响目标受众的创作者。"
    if product_pass:
        summary = (
            f"公开内容同时支持产品用途（{'、'.join(matched_product)}）"
            f"和使用场景（{'、'.join(matched_scene)}）。"
        )
    else:
        missing_groups = terms(evidence_contract.get("missing_groups"))
        summary = f"尚缺产品用途或场景的双重证据：{'、'.join(missing_groups) or '待补正文/字幕/视觉证据'}。"
    return matched_product, matched_scene, product_pass, purpose, summary


def _activation_summary(
    activation_pass: bool,
    activation_status: str,
    sample_count: Any,
    minimum_samples: Any,
) -> str:
    if activation_pass:
        return f"已有 {sample_count} 条近期样本，至少一项可解释的观看或互动信号达到严格门槛。"
    if activation_status == "insufficient_sample":
        return f"目前仅有 {sample_count or 0} 条近期样本；严格判断至少需要 {minimum_samples} 条。"
    if activation_status == "insufficient_metric_sample":
        return (
            f"已返回 {sample_count or 0} 条近期样本，但达到门槛的指标有效观测不足 "
            f"{minimum_samples} 条，仍需补证。"
        )
    if activation_status == "below_floor":
        return f"已有 {sample_count or 0} 条样本，但观看/互动信号尚未达到严格门槛。"
    return "缺少可审计的近期视频聚合，暂不能判断市场推进能力。"


def _audience_context(
    audience_contract: Mapping[str, Any],
    audience_fit: Any,
    *,
    score: Callable[[Any], float | None],
    terms: Callable[..., list[str]],
) -> tuple[float | None, list[str], list[str], str]:
    audience_score = score(audience_fit)
    target_markets = terms(audience_contract.get("target_markets"))
    target_languages = terms(audience_contract.get("target_languages"))
    target = " / ".join(filter(None, ["、".join(target_markets), "、".join(target_languages)]))
    summary = (
        f"目标受众适配分 {audience_score:g}" + (f"，目标为 {target}。" if target else "。")
        if audience_score is not None
        else f"缺少受众市场/语言分布{f'（目标 {target}）' if target else ''}。"
    )
    return audience_score, target_markets, target_languages, summary


def _content_context(
    content_execution: Any,
    *,
    score: Callable[[Any], float | None],
) -> tuple[float | None, str]:
    content_score = score(content_execution)
    summary = (
        f"近期内容执行分 {content_score:g}，可供人工复核制作与持续产出能力。"
        if content_score is not None
        else "缺少近期内容制作、稳定更新或原创度证据。"
    )
    return content_score, summary


def _missing_evidence(
    *,
    product_pass: bool,
    activation_pass: bool,
    activation_status: str,
    minimum_samples: Any,
    audience_score: float | None,
    content_score: float | None,
) -> list[dict[str, Any]]:
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
    return missing


def _next_action(
    activation_status: str, missing: list[dict[str, Any]]
) -> dict[str, Any]:
    if activation_status == "below_floor":
        return {
            "code": "deprioritize_below_activation_floor",
            "label": "样本已足够但观看/互动未达严格门槛；不进入严格名单，业务例外时再人工复核。",
        }
    if missing:
        return {"code": missing[0]["next_action"], "label": missing[0]["label"]}
    return {
        "code": "human_review_before_outreach",
        "label": "人工复核受众、品牌安全与合作目标后，再决定是否联系。",
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
    text: Callable[[Any], str],
    terms: Callable[..., list[str]],
    score: Callable[[Any], float | None],
    card: Callable[..., dict[str, Any]],
    schema: str,
    claim_status: str,
) -> dict[str, Any]:
    matched_product, matched_scene, product_pass, purpose, product_summary = _product_context(
        evidence_contract, terms=terms
    )
    activation_pass = activation_gate.get("passed") is True
    sample_count = activation_gate.get("sample_count")
    minimum_samples = activation_gate.get("minimum_sample_count") or 3
    activation_status = text(activation_gate.get("status")) or "market_activation_missing"
    activation_summary = _activation_summary(
        activation_pass, activation_status, sample_count, minimum_samples
    )
    audience_score, target_markets, target_languages, audience_summary = _audience_context(
        audience_contract, audience_fit, score=score, terms=terms
    )
    content_score, content_summary = _content_context(content_execution, score=score)
    missing_signals = set(content_contract.get("missing_signals") or [])
    cards = [
        card(
            "product_use_and_scene",
            "产品使用需求与场景",
            status="observed" if product_pass else "pending",
            summary=product_summary,
            score=product_use_fit,
            evidence_terms=[*matched_product, *matched_scene],
            evidence_fields=evidence_contract.get("matched_fields"),
        ),
        card(
            "market_activation",
            "市场推进能力",
            status="observed" if activation_pass else "pending",
            summary=activation_summary,
            score=market_activation,
            evidence_fields=list((activation_gate.get("observed_metrics") or {}).keys()),
        ),
        card(
            "audience_fit",
            "受众匹配",
            status="observed" if audience_score is not None else "pending",
            summary=audience_summary,
            score=audience_score,
            evidence_terms=[*target_markets, *target_languages],
        ),
        card(
            "content_execution",
            "内容执行",
            status="observed" if content_score is not None else "pending",
            summary=content_summary,
            score=content_score,
            evidence_fields=[
                key
                for key in (
                    "direct_content_execution",
                    "production_quality",
                    "posting_consistency",
                    "originality",
                )
                if key not in missing_signals
            ],
        ),
    ]
    missing = _missing_evidence(
        product_pass=product_pass,
        activation_pass=activation_pass,
        activation_status=activation_status,
        minimum_samples=minimum_samples,
        audience_score=audience_score,
        content_score=content_score,
    )
    strict_ready = product_pass and activation_pass
    decision_readiness = (
        "decision_support_ready"
        if strict_ready and audience_score is not None and content_score is not None
        else "strict_gate_passed_needs_review"
        if strict_ready
        else "pending_evidence"
    )
    metric_counts = activation_gate.get("metric_sample_counts")
    metric_sufficient = activation_gate.get("metric_sample_sufficient")
    floor_results = activation_gate.get("floor_results")
    activation_evidence = {
        "status": activation_status,
        "sample_count": sample_count,
        "minimum_sample_count": minimum_samples,
        "metric_sample_counts": dict(metric_counts) if isinstance(metric_counts, Mapping) else {},
        "metric_sample_sufficient": dict(metric_sufficient) if isinstance(metric_sufficient, Mapping) else {},
        "floor_results": dict(floor_results) if isinstance(floor_results, Mapping) else {},
        "claim_status": claim_status,
    }
    return {
        "schema": schema,
        "objective": "prospective_growth",
        "purpose": purpose,
        "decision_readiness": decision_readiness,
        "strict_gate_status": "passed" if strict_ready else "blocked",
        "why_find_this_creator": [
            item["summary"] for item in cards if item["status"] == "observed"
        ][:4],
        "reason_cards": cards,
        "activation_evidence": activation_evidence,
        "missing_evidence": missing,
        "next_action": _next_action(activation_status, missing),
        "evidence_confidence": score(evidence_confidence),
        "claim_status": claim_status,
        "conversion_claim": False,
        "outreach_decision": False,
    }
