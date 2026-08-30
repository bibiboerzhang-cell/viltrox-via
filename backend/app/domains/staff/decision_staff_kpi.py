"""Dependency-injected staff KPI aggregation.

This module intentionally stays free of application imports.  The public
``decision_staff.staff_kpi`` wrapper supplies the current domain functions so
tests and callers can continue to monkeypatch the established boundary.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


Rows = list[dict[str, Any]]
SafeRows = Callable[[Any, str, tuple[Any, ...]], Rows]
MergeMetric = Callable[[dict[int, dict[str, Any]], dict[str, Any], str, str], None]


def _base_staff_row(member: dict[str, Any], staff_id: int) -> dict[str, Any]:
    return {
        "staff_id": staff_id,
        "staff_name": member.get("staff_name") or member.get("email") or f"Staff {staff_id}",
        "email": member.get("email") or "",
        "employee_code": member.get("employee_code") or "",
        "avatar_url": member.get("avatar_url"),
        "role": member.get("role") or "readonly",
        "active": int(member.get("active") or 0),
        "kol_claims": 0,
        "projects": 0,
        "active_projects": 0,
        "contacted": 0,
        "replied": 0,
        "agreed": 0,
        "shipped": 0,
        "published": 0,
        "measured": 0,
        "links_created": 0,
        "valid_clicks": 0,
        "bot_clicks": 0,
        "content_views": 0,
        "content_likes": 0,
        "gmv_cents": None,
        "cost_cents": None,
        "net_contribution_cents": None,
        "roi": None,
        "net_roi": None,
        "gmv_data_status": "awaiting_source",
        "cost_data_status": "awaiting_source",
        "net_contribution_data_status": "awaiting_source",
        "roi_data_status": "awaiting_source",
        "financial_data_status": "awaiting_source",
        "ledger_workload_score": 0,
        "kpi_credit": 0,
        "recommendation_source_rows": 0,
        "recommendation_projects": 0,
        "recommendation_published": 0,
        "recommendation_orders": 0,
        "recommendation_clicks": 0,
        "recommendation_gmv_cents": 0,
        "recommendation_cost_cents": 0,
    }


def _seed_staff_rows(base_staff: Rows, staff_id: int | None) -> dict[int, dict[str, Any]]:
    rows_by_staff: dict[int, dict[str, Any]] = {}
    for member in base_staff:
        sid = int(member.get("staff_id") or 0)
        if staff_id and sid != int(staff_id):
            continue
        if sid:
            rows_by_staff[sid] = _base_staff_row(member, sid)
    return rows_by_staff


def _collect_core_metrics(
    rows_by_staff: dict[int, dict[str, Any]],
    *,
    conn: Any,
    start: str,
    staff_id: int | None,
    safe_rows: SafeRows,
    merge_metric: MergeMetric,
) -> None:
    staff_filter = " AND staff_id=?" if staff_id else ""
    params = (start, int(staff_id)) if staff_id else (start,)
    for row in safe_rows(
        conn,
        f"SELECT staff_id, COUNT(*) AS value FROM vkpi_kol_claims WHERE created_at >= ?{staff_filter} GROUP BY staff_id",
        params,
    ):
        merge_metric(rows_by_staff, row, "kol_claims", "value")
    for row in safe_rows(
        conn,
        f"SELECT assigned_staff_id AS staff_id, COUNT(*) AS value FROM vkpi_projects WHERE created_at >= ?{' AND assigned_staff_id=?' if staff_id else ''} GROUP BY assigned_staff_id",
        params,
    ):
        merge_metric(rows_by_staff, row, "projects", "value")
    for row in safe_rows(
        conn,
        f"SELECT assigned_staff_id AS staff_id, COUNT(*) AS value FROM vkpi_projects WHERE stage_status='active' AND updated_at >= ?{' AND assigned_staff_id=?' if staff_id else ''} GROUP BY assigned_staff_id",
        params,
    ):
        merge_metric(rows_by_staff, row, "active_projects", "value")
    for row in safe_rows(
        conn,
        f"""
        SELECT actor_staff_id AS staff_id, to_stage, COUNT(*) AS value
        FROM vkpi_project_stage_events
        WHERE effective_at >= ?{' AND actor_staff_id=?' if staff_id else ''}
        GROUP BY actor_staff_id, to_stage
        """,
        params,
    ):
        stage = str(row.get("to_stage") or "").strip().lower()
        if stage in {"contacted", "replied", "agreed", "shipped", "published", "measured"}:
            merge_metric(rows_by_staff, row, stage, "value")
    for row in safe_rows(
        conn,
        f"SELECT staff_id, COUNT(*) AS value FROM vkpi_links WHERE created_at >= ?{staff_filter} GROUP BY staff_id",
        params,
    ):
        merge_metric(rows_by_staff, row, "links_created", "value")


def _collect_engagement_metrics(
    rows_by_staff: dict[int, dict[str, Any]],
    *,
    conn: Any,
    start: str,
    staff_id: int | None,
    safe_rows: SafeRows,
    merge_metric: MergeMetric,
) -> None:
    params = (start, int(staff_id)) if staff_id else (start,)
    for row in safe_rows(
        conn,
        f"""
        SELECT l.staff_id,
               COALESCE(SUM(CASE WHEN COALESCE(c.is_bot, 0) = 0 THEN 1 ELSE 0 END), 0) AS valid_clicks,
               COALESCE(SUM(CASE WHEN COALESCE(c.is_bot, 0) = 1 THEN 1 ELSE 0 END), 0) AS bot_clicks
        FROM vkpi_link_clicks c
        INNER JOIN vkpi_links l ON l.id = c.link_id
        WHERE c.clicked_at >= ?{' AND l.staff_id=?' if staff_id else ''}
        GROUP BY l.staff_id
        """,
        params,
    ):
        merge_metric(rows_by_staff, row, "valid_clicks", "valid_clicks")
        merge_metric(rows_by_staff, row, "bot_clicks", "bot_clicks")
    for row in safe_rows(
        conn,
        f"""
        SELECT p.assigned_staff_id AS staff_id,
               COALESCE(SUM(kp.views), 0) AS content_views,
               COALESCE(SUM(kp.likes), 0) AS content_likes
        FROM vkpi_projects p
        INNER JOIN kol_posts kp ON kp.kol_id = p.kol_id
        WHERE kp.created_at >= ?{' AND p.assigned_staff_id=?' if staff_id else ''}
        GROUP BY p.assigned_staff_id
        """,
        params,
    ):
        merge_metric(rows_by_staff, row, "content_views", "content_views")
        merge_metric(rows_by_staff, row, "content_likes", "content_likes")


def _mark_real_source(
    rows_by_staff: dict[int, dict[str, Any]],
    row: dict[str, Any],
    *,
    status_key: str,
    count_key: str,
) -> None:
    sid = int(row.get("staff_id") or 0)
    if sid in rows_by_staff and int(row.get("source_count") or 0) > 0:
        rows_by_staff[sid][status_key] = "real"
        rows_by_staff[sid][count_key] = int(row.get("source_count") or 0)


def _collect_financial_metrics(
    rows_by_staff: dict[int, dict[str, Any]],
    *,
    conn: Any,
    start: str,
    staff_id: int | None,
    safe_rows: SafeRows,
    merge_metric: MergeMetric,
    day_bucket: Callable[..., str],
    active_project_filter: Callable[[str], str],
    verified_attribution_sql: Callable[[str], str],
    approved_cost_sql: Callable[[str], str],
) -> None:
    staff_filter = " AND staff_id=?" if staff_id else ""
    params = (start, int(staff_id)) if staff_id else (start,)
    for row in safe_rows(
        conn,
        f"""
        SELECT sa.staff_id, COALESCE(SUM(sa.revenue_cents), 0) AS value,
               COUNT(*) AS source_count
        FROM vkpi_sales_attributions sa
        WHERE {day_bucket('sa.occurred_at', 'sa.imported_at', 'sa.created_at')} >= ?{staff_filter.replace('staff_id', 'sa.staff_id')}
          AND {active_project_filter('sa')}
          AND {verified_attribution_sql('sa')}
        GROUP BY sa.staff_id
        """,
        params,
    ):
        merge_metric(rows_by_staff, row, "gmv_cents", "value")
        _mark_real_source(
            rows_by_staff,
            row,
            status_key="gmv_data_status",
            count_key="gmv_source_count",
        )
    for row in safe_rows(
        conn,
        f"""
        SELECT c.staff_id, COALESCE(SUM(c.amount_cents), 0) AS value,
               COUNT(*) AS source_count
        FROM vkpi_cost_ledger c
        WHERE {approved_cost_sql('c')}
          AND c.incurred_at >= ?{staff_filter.replace('staff_id', 'c.staff_id')}
          AND {active_project_filter('c')}
        GROUP BY c.staff_id
        """,
        params,
    ):
        merge_metric(rows_by_staff, row, "cost_cents", "value")
        _mark_real_source(
            rows_by_staff,
            row,
            status_key="cost_data_status",
            count_key="cost_source_count",
        )


_LEDGER_KEYS = (
    "ledger_workload_score",
    "kpi_credit",
    "recommendation_source_rows",
    "recommendation_projects",
    "recommendation_published",
    "recommendation_orders",
    "recommendation_clicks",
    "recommendation_gmv_cents",
    "recommendation_cost_cents",
)


def _collect_ledger_metrics(
    rows_by_staff: dict[int, dict[str, Any]],
    *,
    conn: Any,
    start: str,
    staff_id: int | None,
    safe_rows: SafeRows,
    current_kpi_ledger_sql: Callable[[], str],
) -> None:
    ledger_staff_filter = " AND staff_id=?" if staff_id else ""
    ledger_params = (start, int(staff_id)) if staff_id else (start,)
    for row in safe_rows(
        conn,
        f"""
        SELECT staff_id,
               COALESCE(SUM(CASE WHEN metric_key='workload_score' THEN metric_value ELSE 0 END), 0) AS ledger_workload_score,
               COALESCE(SUM(CASE WHEN metric_key='kpi_credit' THEN metric_value ELSE 0 END), 0) AS kpi_credit,
               COALESCE(SUM(CASE WHEN substr(metric_key, 1, 15)='recommendation_' THEN 1 ELSE 0 END), 0) AS recommendation_source_rows,
               COALESCE(SUM(CASE WHEN metric_key='recommendation_project_created' THEN metric_value ELSE 0 END), 0) AS recommendation_projects,
               COALESCE(SUM(CASE WHEN metric_key='recommendation_content_published' THEN metric_value ELSE 0 END), 0) AS recommendation_published,
               COALESCE(SUM(CASE WHEN metric_key='recommendation_order_attributed' THEN metric_value ELSE 0 END), 0) AS recommendation_orders,
               COALESCE(SUM(CASE WHEN metric_key='recommendation_clicks' THEN metric_value ELSE 0 END), 0) AS recommendation_clicks,
               COALESCE(SUM(CASE WHEN metric_key='recommendation_gmv_cents' THEN metric_value ELSE 0 END), 0) AS recommendation_gmv_cents,
               COALESCE(SUM(CASE WHEN metric_key='recommendation_cost_cents' THEN metric_value ELSE 0 END), 0) AS recommendation_cost_cents
        FROM vkpi_kpi_ledger
        WHERE ledger_date >= ?{ledger_staff_filter}
          AND {current_kpi_ledger_sql()}
        GROUP BY staff_id
        """,
        ledger_params,
    ):
        sid = int(row.get("staff_id") or 0)
        if sid not in rows_by_staff:
            continue
        target = rows_by_staff[sid]
        for key in _LEDGER_KEYS:
            target[key] = row.get(key) or 0


def _apply_financial_truth(row: dict[str, Any]) -> None:
    gmv_real = str(row.get("gmv_data_status") or "") == "real"
    cost_real = str(row.get("cost_data_status") or "") == "real"
    gmv = int(row.get("gmv_cents") or 0) if gmv_real else None
    cost = int(row.get("cost_cents") or 0) if cost_real else None
    if gmv_real and cost_real:
        row["net_contribution_cents"] = int(gmv) - int(cost)
        row["net_contribution_data_status"] = "real"
        row["financial_data_status"] = "real"
        if int(cost) > 0:
            row["roi"] = round(int(gmv) / int(cost), 4)
            row["net_roi"] = round((int(gmv) - int(cost)) / int(cost), 4)
            row["roi_data_status"] = "real"
        else:
            row["roi"] = None
            row["net_roi"] = None
            row["roi_data_status"] = "unavailable"
        return
    row["gmv_cents"] = gmv
    row["cost_cents"] = cost
    row["net_contribution_cents"] = None
    row["roi"] = None
    row["net_roi"] = None
    row["net_contribution_data_status"] = "awaiting_source"
    row["roi_data_status"] = "awaiting_source"
    row["financial_data_status"] = "partial" if gmv_real or cost_real else "awaiting_source"


def _finalize_rows(rows_by_staff: dict[int, dict[str, Any]]) -> Rows:
    result_rows = []
    for row in rows_by_staff.values():
        _apply_financial_truth(row)
        row["data_status"] = row["financial_data_status"]
        row["metric_statuses"] = {
            "gmv": row["gmv_data_status"],
            "cost": row["cost_data_status"],
            "net_contribution": row["net_contribution_data_status"],
            "roi": row["roi_data_status"],
        }
        row["workload_score"] = (
            int(row.get("kol_claims") or 0) * 2
            + int(row.get("contacted") or 0)
            + int(row.get("replied") or 0) * 2
            + int(row.get("agreed") or 0) * 4
            + int(row.get("shipped") or 0) * 3
            + int(row.get("published") or 0) * 5
            + int(row.get("measured") or 0) * 3
        )
        row["legacy_workload_score"] = row["workload_score"]
        if float(row.get("ledger_workload_score") or 0):
            row["workload_score"] = round(float(row.get("ledger_workload_score") or 0), 4)
        result_rows.append(row)
    result_rows.sort(
        key=lambda item: (int(item.get("gmv_cents") or 0), int(item.get("workload_score") or 0)),
        reverse=True,
    )
    return result_rows


def build_staff_kpi(
    window: str,
    staff_id: int | None,
    *,
    ensure_schema: Callable[[], None],
    window_start: Callable[[str], str],
    get_connection: Callable[[], Any],
    staff_directory: Callable[[], dict[str, Any]],
    safe_rows: SafeRows,
    merge_metric: MergeMetric,
    day_bucket: Callable[..., str],
    active_project_filter: Callable[[str], str],
    verified_attribution_sql: Callable[[str], str],
    approved_cost_sql: Callable[[str], str],
    current_kpi_ledger_sql: Callable[[], str],
) -> dict[str, Any]:
    ensure_schema()
    start = window_start(window)
    conn = get_connection()
    rows_by_staff = _seed_staff_rows(staff_directory().get("staff", []), staff_id)
    shared = {
        "conn": conn,
        "start": start,
        "staff_id": staff_id,
        "safe_rows": safe_rows,
    }
    _collect_core_metrics(rows_by_staff, merge_metric=merge_metric, **shared)
    _collect_engagement_metrics(rows_by_staff, merge_metric=merge_metric, **shared)
    _collect_financial_metrics(
        rows_by_staff,
        merge_metric=merge_metric,
        day_bucket=day_bucket,
        active_project_filter=active_project_filter,
        verified_attribution_sql=verified_attribution_sql,
        approved_cost_sql=approved_cost_sql,
        **shared,
    )
    _collect_ledger_metrics(
        rows_by_staff,
        current_kpi_ledger_sql=current_kpi_ledger_sql,
        **shared,
    )
    return {
        "window": window,
        "start": start,
        "staff_id": staff_id,
        "rows": _finalize_rows(rows_by_staff),
        "kpi_formula": {
            "kol_claim": 2,
            "contacted": 1,
            "replied": 2,
            "agreed": 4,
            "shipped": 3,
            "published": 5,
            "measured": 3,
            "financial_credit": "net_contribution = gmv - cost, roi = gmv / cost",
        },
    }
