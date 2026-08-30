"""Pure threshold and scope decisions for KOL analysis readiness."""
from __future__ import annotations

from typing import Any


THRESHOLDS = {
    "overall_min_video_samples": 5,
    "overall_min_view_count_ratio": 0.8,
    "overall_min_deep_ready": 3,
    "overall_min_deep_ratio": 0.5,
    "minimum_usable_video_samples": 3,
    "minimum_usable_view_count_ratio": 0.5,
    "fresh_max_age_days": 90,
    "stale_after_days": 180,
    "content_fit_min_full_video_proven": 1,
}


def _gap(code: str, *, severity: str, message: str, observed: Any, required: Any) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "observed": observed,
        "required": required,
    }


def _scope(
    *,
    level: str,
    blockers: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    if blockers:
        decision_mode = "abstain"
        recommendation_status = "abstain"
    elif level == "decision_ready":
        decision_mode = "human_decision_support"
        recommendation_status = "decision_support_ready"
    else:
        decision_mode = "human_review_required"
        recommendation_status = "provisional"
    return {
        "level": level,
        "status": level,
        "claim_status": "descriptive_only",
        "decision_mode": decision_mode,
        "recommendation_status": recommendation_status,
        "blocking_gaps": blockers,
        "warnings": warnings,
    }


def overall_gaps(
    *,
    video_total: int,
    view_ratio: float | None,
    deep_ready: int,
    deep_ratio: float | None,
    qa_ready: int,
    freshness_status: str,
    decision_age_days: int | None,
    sample_limit: int | None,
    sample_truncated: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if video_total < THRESHOLDS["minimum_usable_video_samples"]:
        blockers.append(_gap(
            "video_sample_insufficient", severity="blocking",
            message="视频证据样本不足，停止输出总体合作结论。",
            observed=video_total, required=THRESHOLDS["minimum_usable_video_samples"],
        ))
    elif video_total < THRESHOLDS["overall_min_video_samples"]:
        warnings.append(_gap(
            "video_sample_below_decision_target", severity="warning",
            message="样本可作描述，但不足以达到决策支持门槛。",
            observed=video_total, required=THRESHOLDS["overall_min_video_samples"],
        ))
    if view_ratio is None or view_ratio < THRESHOLDS["minimum_usable_view_count_ratio"]:
        blockers.append(_gap(
            "view_count_coverage_insufficient", severity="blocking",
            message="播放量字段覆盖不足，不能可靠比较内容表现。",
            observed=view_ratio, required=THRESHOLDS["minimum_usable_view_count_ratio"],
        ))
    elif view_ratio < THRESHOLDS["overall_min_view_count_ratio"]:
        warnings.append(_gap(
            "view_count_coverage_below_decision_target", severity="warning",
            message="播放量覆盖只够临时判断。",
            observed=view_ratio, required=THRESHOLDS["overall_min_view_count_ratio"],
        ))
    if deep_ready == 0:
        blockers.append(_gap(
            "deep_analysis_missing", severity="blocking",
            message="没有 ready 的 final_v1 深析，必须放弃内容质量结论。",
            observed=0, required=1,
        ))
    elif deep_ready < THRESHOLDS["overall_min_deep_ready"] or (deep_ratio or 0) < THRESHOLDS["overall_min_deep_ratio"]:
        warnings.append(_gap(
            "deep_analysis_coverage_below_decision_target", severity="warning",
            message="深析覆盖可作描述，但不足以支撑稳定决策。",
            observed={"ready": deep_ready, "ratio": deep_ratio},
            required={"ready": THRESHOLDS["overall_min_deep_ready"], "ratio": THRESHOLDS["overall_min_deep_ratio"]},
        ))
    if freshness_status == "stale":
        blockers.append(_gap(
            "evidence_stale", severity="blocking",
            message="证据或深析已过期，停止输出当前合作建议。",
            observed=decision_age_days, required=f"<= {THRESHOLDS['stale_after_days']} days",
        ))
    elif freshness_status != "fresh":
        warnings.append(_gap(
            "freshness_not_decision_ready", severity="warning",
            message="证据时效不足或无法确认。",
            observed=freshness_status, required=f"<= {THRESHOLDS['fresh_max_age_days']} days",
        ))
    if qa_ready == 0 and deep_ready:
        warnings.append(_gap(
            "qa_missing", severity="warning",
            message="深析结果尚无独立关键帧 QA。",
            observed=0, required=1,
        ))
    if sample_truncated:
        warnings.append(_gap(
            "evidence_sample_truncated_at_limit", severity="warning",
            message="活跃视频证据超过分析读取上限，当前比率只代表已披露的前 200 条样本。",
            observed={"sample_count": video_total, "limit": sample_limit},
            required="complete_active_video_evidence_population",
        ))
    return blockers, warnings


def overall_scope_result(
    *,
    blockers: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    video_total: int,
    view_ratio: float | None,
    deep_ready: int,
    deep_ratio: float | None,
    qa_ready: int,
    freshness_status: str,
    sample_truncated: bool,
) -> tuple[str, dict[str, Any]]:
    ready = bool(
        not blockers
        and video_total >= THRESHOLDS["overall_min_video_samples"]
        and (view_ratio or 0) >= THRESHOLDS["overall_min_view_count_ratio"]
        and deep_ready >= THRESHOLDS["overall_min_deep_ready"]
        and (deep_ratio or 0) >= THRESHOLDS["overall_min_deep_ratio"]
        and freshness_status == "fresh"
        and qa_ready >= 1
        and not sample_truncated
    )
    level = "insufficient" if blockers else "decision_ready" if ready else "provisional"
    return level, _scope(level=level, blockers=blockers, warnings=warnings)


def content_scope_result(
    *,
    overall_level: str,
    overall_blockers: list[dict[str, Any]],
    overall_warnings: list[dict[str, Any]],
    full_video_receipts: list[dict[str, Any]],
) -> dict[str, Any]:
    blockers = list(overall_blockers)
    warnings = list(overall_warnings)
    if len(full_video_receipts) < THRESHOLDS["content_fit_min_full_video_proven"]:
        blockers.append(_gap(
            "full_video_coverage_unproven", severity="blocking",
            message="final_v1 已有结果，但缺少原视频全时长覆盖凭证；内容契合结论必须 abstain。",
            observed=len(full_video_receipts), required=THRESHOLDS["content_fit_min_full_video_proven"],
        ))
    level = (
        "insufficient" if overall_level == "insufficient"
        else "decision_ready" if overall_level == "decision_ready" and not blockers
        else "provisional"
    )
    return _scope(level=level, blockers=blockers, warnings=warnings)


def brand_scope_result(
    *,
    brand_counts: dict[str, int],
    brand_types: list[str],
    full_video_receipts: list[dict[str, Any]],
    freshness_status: str,
) -> tuple[bool, dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    timestamped_count = brand_counts["model_detected_with_timestamp_context"]
    if not brand_types:
        blockers.append(_gap(
            "brand_history_evidence_missing", severity="blocking",
            message="未发现品牌历史证据；这不降低新创作者总体可用性，但品牌历史结论必须 abstain。",
            observed=0, required=1,
        ))
    elif timestamped_count == 0:
        warnings.append(_gap(
            "brand_history_not_timestamp_grounded", severity="warning",
            message="品牌记录可作描述，但缺少视频时间戳语境。",
            observed=brand_types, required="model_detected_with_timestamp_context",
        ))
    if timestamped_count and not full_video_receipts:
        warnings.append(_gap(
            "brand_video_source_completeness_unproven", severity="warning",
            message="品牌检测有时间戳语境，但原视频完整性仍未证明。",
            observed=0, required=1,
        ))
    ready = bool(timestamped_count and full_video_receipts and freshness_status == "fresh")
    level = "insufficient" if blockers else "decision_ready" if ready else "provisional"
    return ready, _scope(level=level, blockers=blockers, warnings=warnings)
