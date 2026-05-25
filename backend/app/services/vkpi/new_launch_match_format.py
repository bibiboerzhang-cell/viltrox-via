"""Formatters for new-launch match preview payloads."""
from __future__ import annotations

from typing import Any


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    distribution = payload.get("score_distribution") or {}
    lines = [
        "# P4-1 New Launch Match Dry-Run",
        "",
        f"**Product:** {payload.get('product_query', '')}",
        f"**Target family:** {payload.get('target_family_name', '')}",
        f"**Generated at:** {payload.get('generated_at', '')}",
        "**Mode:** dry_run",
        f"**Provider calls allowed:** {str(bool(payload.get('provider_calls_allowed'))).lower()}",
        f"**Budget scope:** {(payload.get('budget_guard') or {}).get('scope', '')}",
        f"**Estimated provider cost:** {(payload.get('budget_guard') or {}).get('estimated_cost_usd', 0.0)}",
        "",
        "## Summary",
        "",
        f"- Total candidates evaluated: {summary.get('total_candidates_evaluated', 0)}",
        f"- Returned in JSON: {summary.get('returned', 0)}",
        f"- Displayed in Markdown: {summary.get('markdown_display_count', 0)}",
        f"- Excluded blocked/dropped: {summary.get('excluded_blocked_or_dropped', 0)}",
        f"- Excluded low evidence: {summary.get('excluded_low_evidence', 0)}",
        f"- Top score: {summary.get('top_score', 0)}",
        f"- Median score: {summary.get('median_score', 0)}",
        f"- Recommendation reasons attached: {summary.get('reasons_attached', 0)}",
        "",
        "## Score Distribution",
        "",
        f"- P90+: {distribution.get('p90_plus', 0)} candidates",
        f"- P75-P90: {distribution.get('p75_to_p90', 0)} candidates",
        f"- P50-P75: {distribution.get('p50_to_p75', 0)} candidates",
        f"- Below P50: {distribution.get('below_p50', 0)} candidates",
        "",
        "## Top Recommendations",
        "",
    ]
    for idx, item in enumerate(payload.get("markdown_items") or [], start=1):
        handle = item.get("handle") or item.get("display_name") or item.get("kol_entity_uid")
        breakdown = item.get("score_breakdown") or {}
        lines.extend(
            [
                f"### {idx}. @{handle} (score={item.get('score')}, percentile={item.get('percentile_rank')})",
                "",
                f"**Platform:** {item.get('platform', '')}",
                f"**Country:** {item.get('country', '')}",
                f"**Review required:** {str(bool(item.get('review_required'))).lower()}",
                "",
                "**Supporting evidence:**",
            ]
        )
        for evidence in item.get("evidence_pro") or []:
            source = f"{evidence.get('source_table', '')}:{evidence.get('source_id', '')}".strip(":")
            lines.append(f"- {evidence.get('type', '')}: {evidence.get('detail', '')} [{source}]")
        lines.extend(["", "**Concerns:**"])
        concerns = item.get("evidence_con") or []
        if concerns:
            for evidence in concerns:
                lines.append(
                    f"- {evidence.get('type', '')}: {evidence.get('detail', '')} "
                    f"(severity={evidence.get('severity', '')})"
                )
        else:
            lines.append("- None")
        reason = item.get("recommendation_reason") or {}
        if reason:
            lines.extend(
                [
                    "",
                    "**Recommendation reason:**",
                    f"- Short reason: {reason.get('short_reason', '')}",
                    f"- Pitch angle: {reason.get('pitch_angle', '')}",
                    f"- Caution note: {reason.get('caution_note', '')}",
                    f"- Reason mode: {reason.get('mode', '')} ({reason.get('provider', '')}/{reason.get('status', '')})",
                ]
            )
        lines.extend(
            [
                "",
                "**Score breakdown:** "
                f"product={breakdown.get('product_match', 0)} "
                f"cooperation={breakdown.get('cooperation_strength', 0)} "
                f"market={breakdown.get('market_signal', 0)} "
                f"region={breakdown.get('region_match', 0)} "
                f"contact={breakdown.get('contact_availability', 0)} "
                f"recency={breakdown.get('recency_boost', 0)} "
                f"freshness={breakdown.get('data_freshness', 0)} "
                f"-> base={breakdown.get('base', 0)} "
                f"x penalty={breakdown.get('penalty_factor', 1)} "
                f"= {breakdown.get('final', item.get('score'))}",
                "",
                f"[Open in V-KPI: {((item.get('links') or {}).get('open_in_vkpi') or '')}]",
                "",
                "---",
                "",
            ]
        )
    return "\n".join(lines)


def format_preview_summary(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    lines = [
        f"scenario={payload.get('scenario', '')}",
        f"mode={payload.get('mode', '')}",
        f"product_query={payload.get('product_query', '')}",
        f"target_family={payload.get('target_family_name', '')}",
        f"provider_calls_allowed={str(bool(payload.get('provider_calls_allowed'))).lower()}",
        f"budget_scope={(payload.get('budget_guard') or {}).get('scope', '')}",
        f"budget_allowed={str(bool((payload.get('budget_guard') or {}).get('allowed'))).lower()}",
        f"budget_recorded_cost={str(bool((payload.get('budget_guard') or {}).get('recorded_cost'))).lower()}",
        f"persistence_enabled={str(bool((payload.get('persistence') or {}).get('enabled'))).lower()}",
        f"persisted_run_uid={(payload.get('persistence') or {}).get('run_uid', '')}",
        f"persisted_recommendations={(payload.get('persistence') or {}).get('recommendation_count', 0)}",
        f"total_candidates_evaluated={summary.get('total_candidates_evaluated', 0)}",
        f"eligible_after_hard_filters={summary.get('eligible_after_hard_filters', 0)}",
        f"returned={summary.get('returned', 0)}",
        f"markdown_display_count={summary.get('markdown_display_count', 0)}",
        f"excluded_blocked_or_dropped={summary.get('excluded_blocked_or_dropped', 0)}",
        f"excluded_low_evidence={summary.get('excluded_low_evidence', 0)}",
        f"top_score={summary.get('top_score', 0)}",
        f"median_score={summary.get('median_score', 0)}",
        f"llm_reasons_requested={str(bool(summary.get('llm_reasons_requested'))).lower()}",
        f"reasons_attached={summary.get('reasons_attached', 0)}",
    ]
    for idx, item in enumerate((payload.get("items") or [])[:5], start=1):
        lines.append(
            f"sample.{idx}=rank:{item.get('rank')} score:{item.get('score')} "
            f"percentile:{item.get('percentile_rank')} "
            f"{item.get('platform')}:{item.get('handle')} "
            f"pro:{len(item.get('evidence_pro') or [])} con:{len(item.get('evidence_con') or [])}"
        )
    return "\n".join(lines)
