"""Pure JSON-compatible rendering for KOL product-fit previews."""
from __future__ import annotations

from typing import Any, Mapping


def render_markdown(payload: Mapping[str, Any]) -> str:
    summary = payload.get("summary") or {}
    kol = payload.get("kol") or {}
    distribution = payload.get("score_distribution") or {}
    lines = [
        "# P4-6 KOL Product Fit Preview",
        "",
        f"**KOL:** {kol.get('platform', '')}:{kol.get('handle', '')}",
        f"**Display name:** {kol.get('display_name', '')}",
        f"**Generated at:** {payload.get('generated_at', '')}",
        f"**Mode:** {payload.get('mode', 'dry_run')}",
        (
            "**Provider calls allowed:** "
            f"{str(bool(payload.get('provider_calls_allowed'))).lower()}"
        ),
        f"**Budget scope:** {(payload.get('budget_guard') or {}).get('scope', '')}",
        "",
        "## Summary",
        "",
        f"- Total families evaluated: {summary.get('total_families_evaluated', 0)}",
        f"- Returned in JSON: {summary.get('returned', 0)}",
        f"- Displayed in Markdown: {summary.get('markdown_display_count', 0)}",
        (
            "- Excluded inactive/empty family: "
            f"{summary.get('excluded_inactive_or_empty_family', 0)}"
        ),
        f"- Excluded low evidence: {summary.get('excluded_low_evidence', 0)}",
        (
            "- 11D product-fit candidates: "
            f"{summary.get('dimensions11_product_fit_candidates', 0)}"
        ),
        (
            "- 11D product-fit matched families: "
            f"{summary.get('dimensions11_product_fit_matched', 0)}"
        ),
        f"- Top score: {summary.get('top_score', 0)}",
        f"- Median score: {summary.get('median_score', 0)}",
        (
            "- Recommendation reasons attached: "
            f"{summary.get('reasons_attached', 0)}"
        ),
        "",
        "## Score Distribution",
        "",
        f"- P90+: {distribution.get('p90_plus', 0)} candidates",
        f"- P75-P90: {distribution.get('p75_to_p90', 0)} candidates",
        f"- P50-P75: {distribution.get('p50_to_p75', 0)} candidates",
        f"- Below P50: {distribution.get('below_p50', 0)} candidates",
        "",
        "## Top Product Families",
        "",
    ]
    for index, item in enumerate(payload.get("markdown_items") or [], start=1):
        breakdown = item.get("score_breakdown") or {}
        lines.extend(
            [
                (
                    f"### {index}. {item.get('product_family_name', '')} "
                    f"(score={item.get('score')}, "
                    f"percentile={item.get('percentile_rank')})"
                ),
                "",
                f"**Product members:** {item.get('product_member_count', 0)}",
                "",
                "**Supporting evidence:**",
            ]
        )
        for row in item.get("evidence_pro") or []:
            source = f"{row.get('source_table', '')}:{row.get('source_id', '')}".strip(":")
            lines.append(
                f"- {row.get('type', '')}: {row.get('detail', '')} [{source}]"
            )
        lines.extend(["", "**Concerns:**"])
        concerns = item.get("evidence_con") or []
        if concerns:
            for row in concerns:
                lines.append(
                    f"- {row.get('type', '')}: {row.get('detail', '')} "
                    f"(severity={row.get('severity', '')})"
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
                    (
                        f"- Reason mode: {reason.get('mode', '')} "
                        f"({reason.get('provider', '')}/{reason.get('status', '')})"
                    ),
                ]
            )
        lines.extend(
            [
                "",
                "**Score breakdown:** "
                f"historical={breakdown.get('historical_fit', 0)} "
                f"adjacent={breakdown.get('adjacent_product_fit', 0)} "
                f"dimensions11={breakdown.get('dimensions11_product_fit', 0)} "
                f"cooperation={breakdown.get('cooperation_depth', 0)} "
                f"market={breakdown.get('market_activity', 0)} "
                f"contact={breakdown.get('contact_readiness', 0)} "
                f"region={breakdown.get('region_relevance', 0)} "
                f"quality={breakdown.get('data_quality', 0)} "
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


def format_preview_summary(payload: Mapping[str, Any]) -> str:
    summary = payload.get("summary") or {}
    kol = payload.get("kol") or {}
    lines = [
        f"scenario={payload.get('scenario', '')}",
        f"mode={payload.get('mode', '')}",
        f"kol={kol.get('platform', '')}:{kol.get('handle', '')}",
        f"kol_entity_uid={kol.get('kol_entity_uid', '')}",
        f"kol_pool_id={kol.get('kol_pool_id', '')}",
        (
            "provider_calls_allowed="
            f"{str(bool(payload.get('provider_calls_allowed'))).lower()}"
        ),
        f"budget_scope={(payload.get('budget_guard') or {}).get('scope', '')}",
        (
            "budget_allowed="
            f"{str(bool((payload.get('budget_guard') or {}).get('allowed'))).lower()}"
        ),
        (
            "budget_recorded_cost="
            f"{str(bool((payload.get('budget_guard') or {}).get('recorded_cost'))).lower()}"
        ),
        (
            "persistence_enabled="
            f"{str(bool((payload.get('persistence') or {}).get('enabled'))).lower()}"
        ),
        f"persisted_run_uid={(payload.get('persistence') or {}).get('run_uid', '')}",
        (
            "persisted_recommendations="
            f"{(payload.get('persistence') or {}).get('recommendation_count', 0)}"
        ),
        f"total_families_evaluated={summary.get('total_families_evaluated', 0)}",
        f"eligible_after_hard_filters={summary.get('eligible_after_hard_filters', 0)}",
        f"returned={summary.get('returned', 0)}",
        f"markdown_display_count={summary.get('markdown_display_count', 0)}",
        (
            "excluded_inactive_or_empty_family="
            f"{summary.get('excluded_inactive_or_empty_family', 0)}"
        ),
        f"excluded_low_evidence={summary.get('excluded_low_evidence', 0)}",
        (
            "dimensions11_product_fit_candidates="
            f"{summary.get('dimensions11_product_fit_candidates', 0)}"
        ),
        (
            "dimensions11_product_fit_matched="
            f"{summary.get('dimensions11_product_fit_matched', 0)}"
        ),
        f"top_score={summary.get('top_score', 0)}",
        f"median_score={summary.get('median_score', 0)}",
        (
            "llm_reasons_requested="
            f"{str(bool(summary.get('llm_reasons_requested'))).lower()}"
        ),
        f"reasons_attached={summary.get('reasons_attached', 0)}",
    ]
    for index, item in enumerate((payload.get("items") or [])[:5], start=1):
        lines.append(
            f"sample.{index}=rank:{item.get('rank')} score:{item.get('score')} "
            f"percentile:{item.get('percentile_rank')} "
            f"{item.get('product_family_name')} "
            f"pro:{len(item.get('evidence_pro') or [])} "
            f"con:{len(item.get('evidence_con') or [])}"
        )
    return "\n".join(lines)


__all__ = ["format_preview_summary", "render_markdown"]
