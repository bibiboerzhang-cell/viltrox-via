"""Shared helpers for V-KPI decision aggregates."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.shared.vkpi_kpi_evidence import enrich_kpi_source_row

logger = get_logger(__name__)

def _row(row: Any) -> dict[str, Any]:
    return dict(row) if row else {}


def _window_start(window: str) -> str:
    clean = str(window or "month").strip().lower()
    now = datetime.now()
    if clean in {"today", "day", "daily", "1d"}:
        return now.strftime("%Y-%m-%d")
    if clean in {"7d", "week", "weekly"}:
        return datetime.fromtimestamp(now.timestamp() - 6 * 24 * 3600).strftime("%Y-%m-%d")
    if clean in {"30d"}:
        return datetime.fromtimestamp(now.timestamp() - 29 * 24 * 3600).strftime("%Y-%m-%d")
    return now.strftime("%Y-%m-01")


def _safe_rows(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    except Exception as exc:
        logger.warning("vkpi decision rows query failed: %s", exc)
        return []


def _safe_scalar(conn: Any, sql: str, params: tuple[Any, ...] = (), default: int | float = 0) -> int | float:
    try:
        row = conn.execute(sql, params).fetchone()
        if not row:
            return default
        data = dict(row)
        return next(iter(data.values())) or default
    except Exception as exc:
        logger.warning("vkpi decision scalar query failed: %s", exc)
        return default


def _parse_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except Exception as exc:
        logger.warning("vkpi decision json parse failed: %s", exc)
        return {}


_KPI_LABELS: dict[str, str] = {
    "new_kol": "新增 KOL",
    "project_created": "创建项目",
    "link_created": "创建短链",
    "valid_clicks": "有效点击",
    "published_content": "已发布内容",
    "content_views": "内容播放量",
    "revenue_cents": "销售额",
    "cost_cents": "成本",
    "net_contribution_cents": "净贡献",
    "workload_score": "工作量分",
    "kpi_credit": "KPI Credit",
    "recommendation_shortlisted": "推荐入选",
    "recommendation_rejected": "推荐拒绝",
    "recommendation_claimed": "推荐认领",
    "recommendation_project_created": "推荐建项",
    "recommendation_outreach_sent": "推荐触达",
    "recommendation_reply_received": "推荐回复",
    "recommendation_agreement_reached": "推荐合作",
    "recommendation_content_published": "推荐发布",
    "recommendation_order_attributed": "推荐出单",
    "recommendation_clicks": "推荐点击",
    "recommendation_gmv_cents": "推荐销售额",
    "recommendation_cost_cents": "推荐成本",
    "recommendation_roi": "推荐 ROI",
}


def _active_project_filter(alias: str) -> str:
    prefix = f"{alias}." if alias else ""
    return (
        f"({prefix}project_id IS NULL OR EXISTS ("
        f"SELECT 1 FROM vkpi_projects p WHERE p.id = {prefix}project_id "
        "AND COALESCE(p.stage_status, '') != 'deleted'))"
    )


def _staff_kpi_breakdown(conn: Any, staff_id: int, *, start: str, limit: int = 80) -> dict[str, Any]:
    """Grouped KPI source rows for staff profile drilldown.

    This is intentionally built from vkpi_kpi_ledger, not recomputed business
    tables, so the employee detail page explains exactly what was counted.
    """
    grouped = _safe_rows(
        conn,
        """
        SELECT metric_key,
               COUNT(*) AS source_count,
               COALESCE(SUM(metric_value), 0) AS total_value,
               MIN(ledger_date) AS first_date,
               MAX(ledger_date) AS last_date,
               MAX(confidence) AS confidence
        FROM vkpi_kpi_ledger
        WHERE staff_id=? AND ledger_date >= ?
        GROUP BY metric_key
        ORDER BY
            CASE
                WHEN metric_key IN ('kpi_credit', 'workload_score') THEN 0
                WHEN substr(metric_key, 1, 15)='recommendation_' THEN 1
                ELSE 2
            END,
            total_value DESC,
            metric_key ASC
        """,
        (staff_id, start),
    )
    source_rows = _safe_rows(
        conn,
        """
        SELECT kl.*,
               COALESCE(k.channel_name, '') AS kol_name,
               COALESCE(k.platform, '') AS platform,
               COALESCE(p.project_name, '') AS project_name,
               COALESCE(p.product_sku, '') AS product_sku
        FROM vkpi_kpi_ledger kl
        LEFT JOIN kols k ON k.id = kl.kol_id
        LEFT JOIN vkpi_projects p ON p.id = kl.project_id
        WHERE kl.staff_id=? AND kl.ledger_date >= ?
        ORDER BY kl.ledger_date DESC, kl.id DESC
        LIMIT ?
        """,
        (staff_id, start, max(1, min(300, int(limit or 80)))),
    )
    for row in grouped:
        key = str(row.get("metric_key") or "")
        row["metric_label"] = _KPI_LABELS.get(key, key)
        row["is_recommendation_metric"] = key.startswith("recommendation_")
    for row in source_rows:
        key = str(row.get("metric_key") or "")
        metadata = _parse_json(row.get("metadata_json"))
        enriched = enrich_kpi_source_row(conn, {**row, "metadata": metadata})
        row.update(enriched)
        row["metric_label"] = _KPI_LABELS.get(key, key)
        row["metadata"] = enriched.get("metadata") or metadata
        row["evidence"] = {
            "source_type": row.get("source_type"),
            "source_ref": row.get("source_ref"),
            "formula": row["metadata"].get("formula"),
            "components": row["metadata"].get("components"),
            "recommendation_id": row["metadata"].get("recommendation_id"),
            "outcome_id": row["metadata"].get("outcome_id"),
            "launch_id": row["metadata"].get("launch_id"),
            "kol_pool_id": row["metadata"].get("kol_pool_id"),
            "attribution_id": row["metadata"].get("attribution_id"),
            "cost_id": row["metadata"].get("cost_id"),
            "link_id": row["metadata"].get("link_id"),
            "post_id": row["metadata"].get("post_id"),
            "project_id": row.get("project_id"),
            "kol_id": row.get("kol_id"),
            "source_context": row.get("source_context") or {},
        }
        row["is_derived_metric"] = str(row.get("source_type") or "") == "derived_kpi"
        row["is_recommendation_metric"] = key.startswith("recommendation_")
    recommendation_grouped = [row for row in grouped if str(row.get("metric_key") or "").startswith("recommendation_")]
    recommendation_source_rows = [row for row in source_rows if str(row.get("metric_key") or "").startswith("recommendation_")]
    return {
        "start": start,
        "grouped": grouped,
        "source_rows": source_rows,
        "recommendation_grouped": recommendation_grouped,
        "recommendation_source_rows": recommendation_source_rows,
        "source_count": len(source_rows),
    }


def _summary(conn, staff_id: int | None = None) -> dict[str, Any]:
    project_where = "WHERE assigned_staff_id=?" if staff_id else ""
    project_params: tuple[Any, ...] = (staff_id,) if staff_id else ()
    sales_where = "WHERE staff_id=?" if staff_id else ""
    sales_params: tuple[Any, ...] = (staff_id,) if staff_id else ()
    cost_where = "WHERE status!='void' AND staff_id=?" if staff_id else "WHERE status!='void'"
    cost_params: tuple[Any, ...] = (staff_id,) if staff_id else ()
    link_where = "WHERE staff_id=?" if staff_id else ""
    link_params: tuple[Any, ...] = (staff_id,) if staff_id else ()
    projects = _row(conn.execute(f"SELECT COUNT(*) AS total FROM vkpi_projects {project_where}", project_params).fetchone())
    open_projects = _row(
        conn.execute(
            f"SELECT COUNT(*) AS total FROM vkpi_projects {project_where + (' AND' if project_where else 'WHERE')} stage_status='active'",
            project_params,
        ).fetchone()
    )
    links = _row(
        conn.execute(
            f"""
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(click_count), 0) AS clicks,
                   COALESCE(SUM(valid_click_count), 0) AS valid_clicks,
                   COALESCE(SUM(bot_click_count), 0) AS bot_clicks
            FROM vkpi_links
            {link_where}
            """,
            link_params,
        ).fetchone()
    )
    sales_staff_clause = "AND sa.staff_id=?" if staff_id else ""
    sales = _row(
        conn.execute(
            f"""
            SELECT COALESCE(SUM(revenue_cents), 0) AS revenue_cents,
                   COALESCE(SUM(commission_cents), 0) AS commission_cents
            FROM vkpi_sales_attributions sa
            WHERE {_active_project_filter('sa')}
            {sales_staff_clause}
            """,
            sales_params,
        ).fetchone()
    )
    cost_staff_clause = "AND c.staff_id=?" if staff_id else ""
    costs = _row(
        conn.execute(
            f"""
            SELECT COALESCE(SUM(amount_cents), 0) AS cost_cents
            FROM vkpi_cost_ledger c
            WHERE c.status!='void' AND {_active_project_filter('c')}
            {cost_staff_clause}
            """,
            cost_params,
        ).fetchone()
    )
    revenue = int(sales.get("revenue_cents") or 0)
    cost = int(costs.get("cost_cents") or 0)
    today = _window_start("today")
    today_revenue_ex_company = int(_safe_scalar(
        conn,
        """
        SELECT COALESCE(SUM(revenue_cents), 0)
        FROM vkpi_sales_attributions sa
        WHERE COALESCE(NULLIF(occurred_at, ''), imported_at, created_at) >= ?
          AND ({active_filter})
          AND LOWER(COALESCE(evidence_json, '')) NOT LIKE ?
        """.format(active_filter=_active_project_filter('sa')),
        (today, "%company_account%"),
    ) or 0)
    today_likes = int(_safe_scalar(
        conn,
        """
        SELECT COALESCE(SUM(likes), 0)
        FROM kol_posts
        WHERE created_at >= ?
        """,
        (today,),
    ) or 0)
    today_orders = int(_safe_scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM vkpi_sales_attributions sa
        WHERE COALESCE(occurred_at, imported_at, created_at) >= ?
          AND ({active_filter})
        """.format(active_filter=_active_project_filter('sa')),
        (today,),
    ) or 0)
    today_clicks = int(_safe_scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM vkpi_link_clicks
        WHERE clicked_at >= ? AND COALESCE(is_bot, 0) = 0
        """,
        (today,),
    ) or 0)
    return {
        "projects": int(projects.get("total") or 0),
        "open_projects": int(open_projects.get("total") or 0),
        "links": int(links.get("total") or 0),
        "clicks": int(links.get("clicks") or 0),
        "valid_clicks": int(links.get("valid_clicks") or 0),
        "bot_clicks": int(links.get("bot_clicks") or 0),
        "revenue_cents": revenue,
        "cost_cents": cost,
        "net_contribution_cents": revenue - cost,
        "roi": round(revenue / cost, 4) if cost else 0,
        "net_roi": round((revenue - cost) / cost, 4) if cost else 0,
        "today_gmv_ex_company_cents": today_revenue_ex_company,
        "all_gmv_including_company_cents": revenue,
        "today_likes": today_likes,
        "sales_conversion_rate": round(today_orders / today_clicks, 4) if today_clicks else 0,
        "hot_search_score": int(_safe_scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM platform_ingest_events
            WHERE created_at >= ? AND LOWER(COALESCE(event_type, '')) LIKE ?
            """,
            (today, "%search%"),
        ) or 0),
    }
