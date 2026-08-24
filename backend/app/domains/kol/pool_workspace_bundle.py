"""Optional aggregate projection for the KOL Pool workspace bundle."""
from __future__ import annotations

from typing import Any, Callable


def workspace_aggregate_projection(
    *,
    include_aggregates: bool,
    summary_fn: Callable[[], dict[str, Any]],
    facets_fn: Callable[[Any, Any, set[str]], tuple[list[dict[str, Any]], dict[str, int]]],
    conn: Any,
    selection: Any,
    table_columns: set[str],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return summary, count facets and optional response sections.

    The employee cockpit loads low-reach/funnel analytics from the dedicated
    summary endpoint.  Its paged list therefore opts out of these aggregates,
    while the public workspace default remains backward compatible.
    """
    if not include_aggregates:
        return {"total": selection.visible_count}, {}, {}

    summary = summary_fn()
    by_candidate_kind, by_data_status = facets_fn(conn, selection, table_columns)
    countries = (
        summary.get("country_distribution")
        if isinstance(summary.get("country_distribution"), list)
        else []
    )
    count_facets = {
        "by_candidate_kind": [dict(row) for row in by_candidate_kind],
        "by_data_status": by_data_status,
    }
    optional_sections = {
        "summary": summary,
        "filter_options": {
            "platforms": summary.get("by_platform") or [],
            "countries": countries,
            "data_statuses": [
                {"value": "", "label": "全部"},
                {"value": "complete", "label": "已补全"},
                {"value": "missing", "label": "待补全"},
            ],
            "sort_options": [
                {"value": "fit", "label": "V6 Fit"},
                {"value": "followers", "label": "粉丝"},
                {"value": "updated", "label": "最近更新"},
                {"value": "created", "label": "最近创建"},
            ],
        },
        "market_coverage": {"total_countries": len(countries), "items": countries},
    }
    return summary, count_facets, optional_sections
