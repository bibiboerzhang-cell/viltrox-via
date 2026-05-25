"""Formatters for project next-action preview payloads."""
from __future__ import annotations

from typing import Any


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    distribution = payload.get("score_distribution") or {}
    lines = [
        "# P4-11 Project Next-Action Dry-Run",
        "",
        f"**Generated at:** {payload.get('generated_at', '')}",
        "**Mode:** dry_run",
        f"**Provider calls allowed:** {str(bool(payload.get('provider_calls_allowed'))).lower()}",
        f"**Budget scope:** {(payload.get('budget_guard') or {}).get('scope', '')}",
        "",
        "## Summary",
        "",
        f"- Projects evaluated: {summary.get('projects_evaluated', 0)}",
        f"- Returned in JSON: {summary.get('returned', 0)}",
        f"- Displayed in Markdown: {summary.get('markdown_display_count', 0)}",
        f"- Excluded unassigned: {summary.get('excluded_unassigned', 0)}",
        f"- Excluded low evidence: {summary.get('excluded_low_evidence', 0)}",
        f"- Top score: {summary.get('top_score', 0)}",
        f"- Median score: {summary.get('median_score', 0)}",
        f"- Recommendation reasons attached: {summary.get('reasons_attached', 0)}",
        "",
        "## Score Distribution",
        "",
        f"- P90+: {distribution.get('p90_plus', 0)} suggestions",
        f"- P75-P90: {distribution.get('p75_to_p90', 0)} suggestions",
        f"- P50-P75: {distribution.get('p50_to_p75', 0)} suggestions",
        f"- Below P50: {distribution.get('below_p50', 0)} suggestions",
        "",
        "## Top Suggestions",
        "",
    ]
    for idx, item in enumerate(payload.get("markdown_items") or [], start=1):
        breakdown = item.get("score_breakdown") or {}
        lines.extend(
            [
                f"### {idx}. Project #{item.get('project_id')} {item.get('project_name', '')} (score={item.get('score')}, priority={item.get('priority')})",
                "",
                f"**Stage:** {item.get('current_stage', '')}",
                f"**Suggested action:** {item.get('suggested_action', '')}",
                f"**Reason:** {item.get('reason', '')}",
                "",
                "**Supporting evidence:**",
            ]
        )
        for evidence in item.get("evidence_pro") or []:
            lines.append(f"- {evidence.get('type', '')}: {evidence.get('detail', '')}")
        lines.extend(["", "**Concerns:**"])
        concerns = item.get("evidence_con") or []
        if concerns:
            for evidence in concerns:
                lines.append(f"- {evidence.get('type', '')}: {evidence.get('detail', '')} (severity={evidence.get('severity', '')})")
        else:
            lines.append("- None")
        reason = item.get("recommendation_reason") or {}
        if reason:
            lines.extend(
                [
                    "",
                    "**Recommendation reason:**",
                    f"- Short reason: {reason.get('short_reason', '')}",
                    f"- Execution note: {reason.get('execution_note', '')}",
                    f"- Caution note: {reason.get('caution_note', '')}",
                    f"- Reason mode: {reason.get('mode', '')} ({reason.get('provider', '')}/{reason.get('status', '')})",
                ]
            )
        lines.extend(
            [
                "",
                "**Score breakdown:** "
                f"stale={breakdown.get('stage_staleness', 0)} "
                f"missing={breakdown.get('missing_required_artifact', 0)} "
                f"recent={breakdown.get('recent_signal', 0)} "
                f"value={breakdown.get('business_value', 0)} "
                f"risk={breakdown.get('risk_or_blocker', 0)} "
                f"staff={breakdown.get('staff_scope_fit', 0)} "
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
        f"provider_calls_allowed={str(bool(payload.get('provider_calls_allowed'))).lower()}",
        f"budget_scope={(payload.get('budget_guard') or {}).get('scope', '')}",
        f"budget_allowed={str(bool((payload.get('budget_guard') or {}).get('allowed'))).lower()}",
        f"budget_recorded_cost={str(bool((payload.get('budget_guard') or {}).get('recorded_cost'))).lower()}",
        f"persistence_enabled={str(bool((payload.get('persistence') or {}).get('enabled'))).lower()}",
        f"persisted_run_uid={(payload.get('persistence') or {}).get('run_uid', '')}",
        f"persisted_recommendations={(payload.get('persistence') or {}).get('recommendation_count', 0)}",
        f"projects_evaluated={summary.get('projects_evaluated', 0)}",
        f"eligible_after_hard_filters={summary.get('eligible_after_hard_filters', 0)}",
        f"returned={summary.get('returned', 0)}",
        f"markdown_display_count={summary.get('markdown_display_count', 0)}",
        f"excluded_unassigned={summary.get('excluded_unassigned', 0)}",
        f"excluded_low_evidence={summary.get('excluded_low_evidence', 0)}",
        f"top_score={summary.get('top_score', 0)}",
        f"median_score={summary.get('median_score', 0)}",
        f"llm_reasons_requested={str(bool(summary.get('llm_reasons_requested'))).lower()}",
        f"reasons_attached={summary.get('reasons_attached', 0)}",
    ]
    for idx, item in enumerate((payload.get("items") or [])[:5], start=1):
        lines.append(
            f"sample.{idx}=rank:{item.get('rank')} score:{item.get('score')} "
            f"priority:{item.get('priority')} project:{item.get('project_id')} "
            f"stage:{item.get('current_stage')} action:{item.get('suggested_action')} "
            f"pro:{len(item.get('evidence_pro') or [])} con:{len(item.get('evidence_con') or [])}"
        )
    return "\n".join(lines)
