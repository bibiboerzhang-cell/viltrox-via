"""Low-complexity builders for one data-completion priority item."""
from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence


def anchor_coverage(
    specs: Sequence[Any],
    raw_anchor_hits: Any,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    raw = raw_anchor_hits if isinstance(raw_anchor_hits, Mapping) else {}
    coverage: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for spec in specs:
        sources = raw.get(spec.key) if isinstance(raw, Mapping) else {}
        source_status = {
            "factual_profile": bool((sources or {}).get("factual_profile")),
            "video_evidence": bool((sources or {}).get("video_evidence")),
            "final_v1": bool((sources or {}).get("final_v1")),
        }
        observed = any(source_status.values())
        coverage[spec.key] = {"observed": observed, "sources": source_status}
        if not observed:
            missing.append(spec.key)
    return coverage, missing


def _field_actions(
    field_status: Mapping[str, str],
    followers_status: str,
    action: Callable[..., dict[str, Any]],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if field_status["platform"] != "known":
        actions.append(action("platform_missing", 24, "verify_platform_identity", "平台为空，平台硬筛无法判定。", "low", "critical", "hard_filter_eligibility", "恢复平台筛选资格判定"))
    if field_status["country"] != "known":
        actions.append(action("country_missing", 18, "verify_creator_country", "国家/地区未知，会从地区硬筛中被诚实排除。", "low", "high", "hard_filter_eligibility", "恢复国家/地区筛选资格判定"))
    if field_status["language"] != "known":
        actions.append(action("language_missing", 16, "verify_content_language", "内容语言未知，会从语言硬筛中被诚实排除。", "low", "high", "hard_filter_eligibility", "恢复语言筛选资格判定"))
    if followers_status != "known":
        reason = "粉丝量缺失，触达门槛无法判定。" if followers_status == "missing" else "粉丝量为零或非法，需核验是否为采集占位值。"
        actions.append(action("followers_unverified", 20, "refresh_profile_reach_metrics", reason, "low", "high", "hard_filter_eligibility", "恢复粉丝门槛与批量筛选资格判定"))
    return actions


def _evidence_actions(
    *,
    specs: Sequence[Any],
    missing_anchors: list[str],
    evidence_count: int,
    view_known: int,
    view_ratio: float | None,
    video_target: int,
    required_view_ratio: float,
    action: Callable[..., dict[str, Any]],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if specs and missing_anchors:
        fraction = len(missing_anchors) / len(specs)
        actions.append(action("required_product_anchor_missing", 30 * fraction, "collect_product_specific_video_evidence", f"必需产品锚点缺 {len(missing_anchors)}/{len(specs)}：{', '.join(missing_anchors)}。", "medium", "critical", "relevance_gate", "使严格产品匹配可以由事实/视频证据支持，而非依赖派生画像"))
    if evidence_count == 0:
        actions.append(action("video_evidence_missing", 24, "collect_representative_video_evidence", "没有可用视频证据，相关度与内容质量均无法核验。", "medium", "critical", "relevance_and_quality_gate", "建立产品锚点、场景和内容质量的事实底座"))
    elif evidence_count < video_target:
        gap = video_target - evidence_count
        actions.append(action("video_sample_insufficient", 10 * gap / video_target, "collect_more_representative_videos", f"视频样本 {evidence_count}/{video_target}，不足以达到既有就绪度样本门槛。", "medium", "medium", "analysis_readiness", "降低单条视频偶然性并扩大内容覆盖"))
    if evidence_count > 0 and (view_ratio or 0) < required_view_ratio:
        shortfall = max(0.0, required_view_ratio - (view_ratio or 0)) / required_view_ratio
        actions.append(action("view_count_coverage_insufficient", 12 * shortfall, "refresh_video_view_counts", f"播放量已知 {view_known}/{evidence_count}，低于 {int(required_view_ratio * 100)}% 就绪度门槛。", "low", "medium", "content_quality", "恢复代表作和表现质量判断的可比性"))
    return actions


def _comment_actions(
    *,
    evidence_bridge_comments: int,
    direct_comments: int,
    stored_comments: int,
    comment_metric_known: int,
    action: Callable[..., dict[str, Any]],
) -> list[dict[str, Any]]:
    if evidence_bridge_comments <= 0 and direct_comments > 0:
        return [action("comments_bridge_unverified", 4, "verify_comment_kol_identity_bridge", "评论仅通过 account_id 同号桥接，可能与 KOL Pool 主键碰撞，不能直接当作该创作者受众证据。", "low", "medium", "engagement_quality", "确认评论样本确实属于该创作者后再用于互动与受众判断")]
    if stored_comments <= 0 and comment_metric_known <= 0:
        return [action("comments_missing", 8, "collect_representative_comments", "既无评论样本，也无视频评论量元数据。", "medium", "medium", "engagement_quality", "支持互动质量、受众意图与真实性复核")]
    if evidence_bridge_comments <= 0:
        return [action("comment_text_missing", 4, "collect_representative_comments", "已有评论量元数据，但没有可审阅的评论样本。", "medium", "low", "engagement_quality", "从数量判断升级到评论内容与真实性判断")]
    return []


def _analysis_actions(
    *,
    audience: Mapping[str, Any],
    final_count: int,
    final_target: int,
    action: Callable[..., dict[str, Any]],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if audience["status"] != "ready":
        actions.append(action("audience_profile_missing", 10, "build_audience_ensemble", "缺少有样本的 ensemble_v1 受众画像。", "medium", "medium", "audience_fit", "支持受众地区、语言与目标市场匹配判断"))
    if final_count < final_target:
        gap = final_target - final_count
        actions.append(action("final_v1_insufficient", 15 * gap / final_target, "analyze_high_value_videos_after_evidence_review", f"ready final_v1 为 {final_count}/{final_target}；应先人工确认视频证据再进入高成本深析。", "high", "medium", "deep_content_quality", "支持完整内容、品牌提及和合作风险判断"))
    return actions


def priority_actions(
    *,
    field_status: Mapping[str, str],
    followers_status: str,
    specs: Sequence[Any],
    missing_anchors: list[str],
    evidence_count: int,
    view_known: int,
    view_ratio: float | None,
    evidence_bridge_comments: int,
    direct_comments: int,
    stored_comments: int,
    comment_metric_known: int,
    audience: Mapping[str, Any],
    final_count: int,
    video_target: int,
    required_view_ratio: float,
    final_target: int,
    action: Callable[..., dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        *_field_actions(field_status, followers_status, action),
        *_evidence_actions(
            specs=specs,
            missing_anchors=missing_anchors,
            evidence_count=evidence_count,
            view_known=view_known,
            view_ratio=view_ratio,
            video_target=video_target,
            required_view_ratio=required_view_ratio,
            action=action,
        ),
        *_comment_actions(
            evidence_bridge_comments=evidence_bridge_comments,
            direct_comments=direct_comments,
            stored_comments=stored_comments,
            comment_metric_known=comment_metric_known,
            action=action,
        ),
        *_analysis_actions(
            audience=audience,
            final_count=final_count,
            final_target=final_target,
            action=action,
        ),
    ]


def priority_band(score: float) -> str:
    if score >= 60:
        return "urgent"
    if score >= 40:
        return "high"
    if score >= 20:
        return "medium"
    return "low"


def comment_evidence_status(
    evidence_bridge_comments: int,
    direct_comments: int,
    comment_metric_known: int,
) -> str:
    if evidence_bridge_comments > 0:
        return "evidence_linked"
    if direct_comments > 0:
        return "account_bridge_unverified"
    if comment_metric_known > 0:
        return "metrics_only"
    return "missing"
