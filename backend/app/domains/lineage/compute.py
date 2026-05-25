"""Metric computation functions for V-KPI metric lineage snapshots."""
from __future__ import annotations

from typing import Any

from app.domains.lineage.definitions import METRICS
from app.domains.lineage.common import _float, _int

def _scope_clause(scope_type: str, scope_id: int | None, table_alias: str = "") -> tuple[str, tuple[Any, ...]]:
    """Generate a SQL fragment + params for the given scope.

    Reused by every primary metric computer.
    """
    prefix = f"{table_alias}." if table_alias else ""
    if scope_type == "staff" and scope_id:
        return f" AND {prefix}staff_id = ? ", (int(scope_id),)
    if scope_type == "project" and scope_id:
        return f" AND {prefix}project_id = ? ", (int(scope_id),)
    if scope_type == "kol" and scope_id:
        return f" AND {prefix}kol_id = ? ", (int(scope_id),)
    return "", ()

def _active_project_filter(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return (
        f" AND ({prefix}project_id IS NULL OR EXISTS ("
        f"SELECT 1 FROM vkpi_projects p WHERE p.id = {prefix}project_id "
        "AND COALESCE(p.stage_status, '') != 'deleted')) "
    )

def _table_exists(conn: Any, table_name: str) -> bool:
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        return bool(row)
    except Exception:
        row = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        return bool(row)

def _compute_metric(
    conn: Any,
    *,
    metric_key: str,
    period_start: str,
    period_end: str,
    scope_type: str,
    scope_id: int | None,
) -> dict[str, Any]:
    """Compute one primary metric and capture its source rows."""
    if metric_key == "gmv":
        return _compute_gmv(conn, period_start, period_end, scope_type, scope_id)
    if metric_key == "cost":
        return _compute_cost(conn, period_start, period_end, scope_type, scope_id)
    if metric_key == "new_kol":
        return _compute_new_kol(conn, period_start, period_end, scope_type, scope_id)
    if metric_key == "published_content":
        return _compute_published_content(conn, period_start, period_end, scope_type, scope_id)
    if metric_key == "valid_clicks":
        return _compute_valid_clicks(conn, period_start, period_end, scope_type, scope_id)
    if metric_key == "views":
        return _compute_views(conn, period_start, period_end, scope_type, scope_id)
    if metric_key == "active_projects":
        return _compute_active_projects(conn, period_start, period_end, scope_type, scope_id)
    if metric_key == "alerts":
        return _compute_alerts(conn, period_start, period_end, scope_type, scope_id)
    raise ValueError(f"no compute handler for metric: {metric_key}")

def _compute_gmv(conn, period_start, period_end, scope_type, scope_id) -> dict[str, Any]:
    where_extra, params_extra = _scope_clause(scope_type, scope_id, "sa")
    rows = conn.execute(
        f"""
        SELECT sa.id, sa.source_platform, sa.source_ref, sa.project_id, sa.kol_id, sa.staff_id,
               sa.revenue_cents, sa.currency, sa.occurred_at, sa.confidence, sa.order_id,
               sa.link_id, sa.product_sku, sa.attribution_model, sa.evidence_json,
               sa.shopify_order_snapshot_id,
               os.shopify_order_id, os.order_name, os.order_number,
               os.processed_at AS shopify_processed_at,
               os.currency AS shopify_currency,
               os.subtotal_cents AS shopify_subtotal_cents,
               os.total_cents AS shopify_total_cents,
               os.financial_status, os.fulfillment_status, os.refund_status,
               os.discount_codes_json, os.landing_site,
               os.note_attributes_json, os.line_items_json, os.raw_payload_hash
        FROM vkpi_sales_attributions sa
        LEFT JOIN vkpi_shopify_order_snapshots os ON os.id = sa.shopify_order_snapshot_id
        WHERE COALESCE(sa.occurred_at, sa.imported_at, sa.created_at) BETWEEN ? AND ?
        {_active_project_filter('sa')}
        {where_extra}
        ORDER BY sa.occurred_at DESC, sa.id DESC
        """,
        (period_start, period_end, *params_extra),
    ).fetchall()

    total = 0
    sources = []
    for row in rows:
        data = dict(row)
        amount = _int(data.get("revenue_cents"))
        total += amount
        sources.append({
            "source_type": "sales_attribution",
            "source_id": _int(data.get("id")),
            "contribution_amount": amount,
            "evidence_type": str(data.get("source_platform") or "shopify_order"),
            "evidence_ref": str(data.get("source_ref") or ""),
            "project_id": _int(data.get("project_id")) or None,
            "kol_id": _int(data.get("kol_id")) or None,
            "staff_id": _int(data.get("staff_id")) or None,
            "occurred_at": data.get("occurred_at"),
            "snapshot": {
                "currency": data.get("currency"),
                "confidence": data.get("confidence"),
                "order_id": data.get("order_id"),
                "link_id": data.get("link_id"),
                "product_sku": data.get("product_sku"),
                "attribution_model": data.get("attribution_model"),
                "shopify_order_snapshot_id": _int(data.get("shopify_order_snapshot_id")) or None,
                "shopify_order": {
                    "id": _int(data.get("shopify_order_snapshot_id")) or None,
                    "shopify_order_id": data.get("shopify_order_id"),
                    "order_name": data.get("order_name"),
                    "order_number": data.get("order_number"),
                    "processed_at": data.get("shopify_processed_at"),
                    "currency": data.get("shopify_currency"),
                    "subtotal_cents": _int(data.get("shopify_subtotal_cents")),
                    "total_cents": _int(data.get("shopify_total_cents")),
                    "financial_status": data.get("financial_status"),
                    "fulfillment_status": data.get("fulfillment_status"),
                    "refund_status": data.get("refund_status"),
                    "discount_codes_json": data.get("discount_codes_json"),
                    "landing_site": data.get("landing_site"),
                    "note_attributes_json": data.get("note_attributes_json"),
                    "line_items_json": data.get("line_items_json"),
                    "raw_payload_hash": data.get("raw_payload_hash"),
                } if data.get("shopify_order_snapshot_id") else None,
            },
        })

    return {
        "value_numeric": total,
        "currency": "USD",
        "unit": "cents",
        "calculation": {"formula": METRICS["gmv"]["formula"], "row_count": len(sources), "total_cents": total},
        "sources": sources,
    }

def _compute_cost(conn, period_start, period_end, scope_type, scope_id) -> dict[str, Any]:
    where_extra, params_extra = _scope_clause(scope_type, scope_id, "c")
    rows = conn.execute(
        f"""
        SELECT id, project_id, kol_id, staff_id, cost_type, amount_cents,
               currency, status, incurred_at, source_ref, note
        FROM vkpi_cost_ledger c
        WHERE status != 'void'
          AND incurred_at BETWEEN ? AND ?
        {_active_project_filter('c')}
        {where_extra}
        ORDER BY incurred_at DESC, id DESC
        """,
        (period_start, period_end, *params_extra),
    ).fetchall()

    total = 0
    sources = []
    for row in rows:
        data = dict(row)
        amount = _int(data.get("amount_cents"))
        total += amount
        sources.append({
            "source_type": "cost_ledger",
            "source_id": _int(data.get("id")),
            "contribution_amount": amount,
            "evidence_type": str(data.get("cost_type") or "cost_invoice"),
            "evidence_ref": str(data.get("source_ref") or ""),
            "project_id": _int(data.get("project_id")) or None,
            "kol_id": _int(data.get("kol_id")) or None,
            "staff_id": _int(data.get("staff_id")) or None,
            "occurred_at": data.get("incurred_at"),
            "snapshot": {
                "currency": data.get("currency"),
                "status": data.get("status"),
                "cost_type": data.get("cost_type"),
                "note": data.get("note"),
            },
        })

    return {
        "value_numeric": total,
        "currency": "USD",
        "unit": "cents",
        "calculation": {"formula": METRICS["cost"]["formula"], "row_count": len(sources), "total_cents": total},
        "sources": sources,
    }

def _compute_new_kol(conn, period_start, period_end, scope_type, scope_id) -> dict[str, Any]:
    where_extra, params_extra = _scope_clause(scope_type, scope_id)
    rows = conn.execute(
        f"""
        SELECT id, kol_id, staff_id, project_id, status, claimed_at, expires_at, release_reason
        FROM vkpi_kol_claims
        WHERE created_at BETWEEN ? AND ?
        {where_extra}
        ORDER BY created_at DESC, id DESC
        """,
        (period_start, period_end, *params_extra),
    ).fetchall()
    sources = []
    for row in rows:
        data = dict(row)
        sources.append({
            "source_type": "claim",
            "source_id": _int(data.get("id")),
            "contribution_amount": 1,
            "evidence_type": "claim",
            "evidence_ref": str(data.get("status") or ""),
            "project_id": _int(data.get("project_id")) or None,
            "kol_id": _int(data.get("kol_id")) or None,
            "staff_id": _int(data.get("staff_id")) or None,
            "occurred_at": data.get("claimed_at"),
            "snapshot": {"status": data.get("status"), "expires_at": data.get("expires_at"), "release_reason": data.get("release_reason")},
        })
    return {
        "value_numeric": len(sources),
        "currency": "",
        "unit": "count",
        "calculation": {"formula": METRICS["new_kol"]["formula"], "count": len(sources)},
        "sources": sources,
    }

def _compute_published_content(conn, period_start, period_end, scope_type, scope_id) -> dict[str, Any]:
    # stage_event scope filter is on actor_staff_id (not staff_id)
    where_extra = ""
    params_extra: tuple[Any, ...] = ()
    if scope_type == "staff" and scope_id:
        where_extra = " AND e.actor_staff_id = ? "
        params_extra = (int(scope_id),)
    elif scope_type == "project" and scope_id:
        where_extra = " AND e.project_id = ? "
        params_extra = (int(scope_id),)
    elif scope_type == "kol" and scope_id:
        where_extra = " AND p.kol_id = ? "
        params_extra = (int(scope_id),)

    rows = conn.execute(
        f"""
        SELECT e.id, e.project_id, e.actor_staff_id AS staff_id, e.from_stage,
               e.to_stage, e.effective_at, e.note, e.source_ref_type, e.source_ref_id,
               p.kol_id, p.project_name
        FROM vkpi_project_stage_events e
        LEFT JOIN vkpi_projects p ON p.id = e.project_id
        WHERE e.to_stage IN ('published','posted')
          AND e.effective_at BETWEEN ? AND ?
        {where_extra}
        ORDER BY e.effective_at DESC, e.id DESC
        """,
        (period_start, period_end, *params_extra),
    ).fetchall()
    sources = []
    for row in rows:
        data = dict(row)
        sources.append({
            "source_type": "stage_event",
            "source_id": _int(data.get("id")),
            "contribution_amount": 1,
            "evidence_type": "stage_event",
            "evidence_ref": str(data.get("source_ref_id") or ""),
            "project_id": _int(data.get("project_id")) or None,
            "kol_id": _int(data.get("kol_id")) or None,
            "staff_id": _int(data.get("staff_id")) or None,
            "occurred_at": data.get("effective_at"),
            "snapshot": {
                "from_stage": data.get("from_stage"),
                "to_stage": data.get("to_stage"),
                "project_name": data.get("project_name"),
                "note": data.get("note"),
                "source_ref_type": data.get("source_ref_type"),
            },
        })

    # Content rows are stronger evidence than a stage-only event when the team
    # has captured a real post URL. Include them as source rows without showing
    # placeholders when there is no content data.
    if _table_exists(conn, "vkpi_content_posts"):
        content_where = ""
        content_params: list[Any] = []
        if scope_type == "staff" and scope_id:
            content_where = (
                " AND (p.assigned_staff_id = ? OR EXISTS ("
                "SELECT 1 FROM vkpi_kol_claims cl "
                "WHERE cl.kol_id = cp.kol_id AND cl.staff_id = ? AND cl.status = 'active'"
                ")) "
            )
            content_params.extend([int(scope_id), int(scope_id)])
        elif scope_type == "project" and scope_id:
            content_where = " AND cp.project_id = ? "
            content_params.append(int(scope_id))
        elif scope_type == "kol" and scope_id:
            content_where = " AND cp.kol_id = ? "
            content_params.append(int(scope_id))
        content_rows = conn.execute(
            f"""
            SELECT cp.id, cp.project_id, cp.kol_id, cp.link_id, cp.platform,
                   cp.post_url, cp.title, cp.published_at, cp.content_type,
                   cp.views, cp.likes, cp.comments, cp.shares,
                   p.assigned_staff_id AS staff_id, p.project_name
            FROM vkpi_content_posts cp
            LEFT JOIN vkpi_projects p ON p.id = cp.project_id
            WHERE COALESCE(cp.published_at, cp.created_at) BETWEEN ? AND ?
              AND (cp.project_id IS NULL OR COALESCE(p.stage_status, '') != 'deleted')
            {content_where}
            ORDER BY COALESCE(cp.published_at, cp.created_at) DESC, cp.id DESC
            """,
            (period_start, period_end, *content_params),
        ).fetchall()
        content_project_ids = {_int(dict(row).get("project_id")) for row in content_rows if _int(dict(row).get("project_id"))}
        if content_project_ids:
            sources = [
                src for src in sources
                if not (src.get("source_type") == "stage_event" and _int(src.get("project_id")) in content_project_ids)
            ]
        seen = {f"{src.get('source_type')}:{src.get('source_id')}" for src in sources}
        for row in content_rows:
            data = dict(row)
            source_key = f"content_post:{_int(data.get('id'))}"
            if source_key in seen:
                continue
            sources.append({
                "source_type": "content_post",
                "source_id": _int(data.get("id")),
                "contribution_amount": 1,
                "evidence_type": "content_post",
                "evidence_ref": str(data.get("post_url") or ""),
                "project_id": _int(data.get("project_id")) or None,
                "kol_id": _int(data.get("kol_id")) or None,
                "staff_id": _int(data.get("staff_id")) or None,
                "occurred_at": data.get("published_at"),
                "snapshot": {
                    "platform": data.get("platform"),
                    "title": data.get("title"),
                    "content_type": data.get("content_type"),
                    "project_name": data.get("project_name"),
                    "views": _int(data.get("views")),
                    "likes": _int(data.get("likes")),
                    "comments": _int(data.get("comments")),
                    "shares": _int(data.get("shares")),
                },
            })
    return {
        "value_numeric": len(sources),
        "currency": "",
        "unit": "count",
        "calculation": {"formula": METRICS["published_content"]["formula"], "count": len(sources)},
        "sources": sources,
    }

def _compute_valid_clicks(conn, period_start, period_end, scope_type, scope_id) -> dict[str, Any]:
    # link_click scope: filter via vkpi_links.staff_id / project_id / kol_id
    join_filter = ""
    params_extra: tuple[Any, ...] = ()
    if scope_type == "staff" and scope_id:
        join_filter = " AND l.staff_id = ? "
        params_extra = (int(scope_id),)
    elif scope_type == "project" and scope_id:
        join_filter = " AND l.project_id = ? "
        params_extra = (int(scope_id),)
    elif scope_type == "kol" and scope_id:
        join_filter = " AND l.kol_id = ? "
        params_extra = (int(scope_id),)

    rows = conn.execute(
        f"""
        SELECT c.id, c.link_id, c.event_id, c.clicked_at, c.country_code,
               c.device_type, c.referrer, c.session_id, c.is_bot,
               l.project_id, l.kol_id, l.staff_id, l.slug, l.campaign_name
        FROM vkpi_link_clicks c
        INNER JOIN vkpi_links l ON l.id = c.link_id
        WHERE COALESCE(c.is_bot,0) = 0
          AND c.clicked_at BETWEEN ? AND ?
        {join_filter}
        ORDER BY c.clicked_at DESC, c.id DESC
        LIMIT 5000
        """,
        (period_start, period_end, *params_extra),
    ).fetchall()
    sources = []
    for row in rows:
        data = dict(row)
        sources.append({
            "source_type": "link_click",
            "source_id": _int(data.get("id")),
            "contribution_amount": 1,
            "evidence_type": "link_click",
            "evidence_ref": str(data.get("event_id") or ""),
            "project_id": _int(data.get("project_id")) or None,
            "kol_id": _int(data.get("kol_id")) or None,
            "staff_id": _int(data.get("staff_id")) or None,
            "occurred_at": data.get("clicked_at"),
            "snapshot": {
                "slug": data.get("slug"),
                "campaign_name": data.get("campaign_name"),
                "country_code": data.get("country_code"),
                "device_type": data.get("device_type"),
                "referrer": data.get("referrer"),
            },
        })
    return {
        "value_numeric": len(sources),
        "currency": "",
        "unit": "count",
        "calculation": {"formula": METRICS["valid_clicks"]["formula"], "count": len(sources)},
        "sources": sources,
    }

def _compute_views(conn, period_start, period_end, scope_type, scope_id) -> dict[str, Any]:
    sources: list[dict[str, Any]] = []
    total = 0
    if _table_exists(conn, "vkpi_content_posts"):
        where = ""
        params_extra: list[Any] = []
        if scope_type == "staff" and scope_id:
            where = (
                " AND (p.assigned_staff_id = ? OR EXISTS ("
                "SELECT 1 FROM vkpi_kol_claims cl "
                "WHERE cl.kol_id = cp.kol_id AND cl.staff_id = ? AND cl.status = 'active'"
                ")) "
            )
            params_extra.extend([int(scope_id), int(scope_id)])
        elif scope_type == "project" and scope_id:
            where = " AND cp.project_id = ? "
            params_extra.append(int(scope_id))
        elif scope_type == "kol" and scope_id:
            where = " AND cp.kol_id = ? "
            params_extra.append(int(scope_id))
        rows = conn.execute(
            f"""
            SELECT cp.id, cp.project_id, cp.kol_id, cp.link_id, cp.platform,
                   cp.post_url, cp.title, cp.published_at, cp.content_type,
                   cp.views, cp.likes, cp.comments, cp.shares,
                   p.assigned_staff_id AS staff_id, p.project_name
            FROM vkpi_content_posts cp
            LEFT JOIN vkpi_projects p ON p.id = cp.project_id
            WHERE COALESCE(cp.published_at, cp.created_at) BETWEEN ? AND ?
              AND COALESCE(cp.views, 0) > 0
              AND (cp.project_id IS NULL OR COALESCE(p.stage_status, '') != 'deleted')
            {where}
            ORDER BY cp.views DESC, COALESCE(cp.published_at, cp.created_at) DESC
            LIMIT 5000
            """,
            (period_start, period_end, *params_extra),
        ).fetchall()
        for row in rows:
            data = dict(row)
            amount = _int(data.get("views"))
            total += amount
            sources.append({
                "source_type": "content_post",
                "source_id": _int(data.get("id")),
                "contribution_amount": amount,
                "evidence_type": "content_post",
                "evidence_ref": str(data.get("post_url") or ""),
                "project_id": _int(data.get("project_id")) or None,
                "kol_id": _int(data.get("kol_id")) or None,
                "staff_id": _int(data.get("staff_id")) or None,
                "occurred_at": data.get("published_at"),
                "snapshot": {
                    "platform": data.get("platform"),
                    "title": data.get("title"),
                    "content_type": data.get("content_type"),
                    "project_name": data.get("project_name"),
                    "likes": _int(data.get("likes")),
                    "comments": _int(data.get("comments")),
                    "shares": _int(data.get("shares")),
                },
            })
    return {
        "value_numeric": total,
        "currency": "",
        "unit": "count",
        "calculation": {"formula": METRICS["views"]["formula"], "row_count": len(sources), "total_views": total},
        "sources": sources,
    }

def _compute_active_projects(conn, period_start, period_end, scope_type, scope_id) -> dict[str, Any]:
    where = ""
    params_extra: list[Any] = []
    if scope_type == "staff" and scope_id:
        where = " AND p.assigned_staff_id = ? "
        params_extra.append(int(scope_id))
    elif scope_type == "project" and scope_id:
        where = " AND p.id = ? "
        params_extra.append(int(scope_id))
    elif scope_type == "kol" and scope_id:
        where = " AND p.kol_id = ? "
        params_extra.append(int(scope_id))
    rows = conn.execute(
        f"""
        SELECT p.id, p.project_uid, p.project_name, p.kol_id,
               p.assigned_staff_id AS staff_id, p.product_sku, p.product_name,
               p.platform, p.stage, p.stage_status, p.started_at,
               p.last_activity_at, p.created_at, p.updated_at
        FROM vkpi_projects p
        WHERE COALESCE(p.stage_status, '') != 'deleted'
          AND LOWER(COALESCE(p.stage, '')) NOT IN ('closed','lost','released','cancelled')
        {where}
        ORDER BY COALESCE(p.last_activity_at, p.updated_at, p.created_at) DESC, p.id DESC
        LIMIT 5000
        """,
        tuple(params_extra),
    ).fetchall()
    sources = []
    for row in rows:
        data = dict(row)
        sources.append({
            "source_type": "project",
            "source_id": _int(data.get("id")),
            "contribution_amount": 1,
            "evidence_type": "project",
            "evidence_ref": str(data.get("project_uid") or data.get("project_name") or ""),
            "project_id": _int(data.get("id")) or None,
            "kol_id": _int(data.get("kol_id")) or None,
            "staff_id": _int(data.get("staff_id")) or None,
            "occurred_at": data.get("last_activity_at") or data.get("updated_at") or data.get("created_at"),
            "snapshot": {
                "project_name": data.get("project_name"),
                "product_sku": data.get("product_sku"),
                "product_name": data.get("product_name"),
                "platform": data.get("platform"),
                "stage": data.get("stage"),
                "stage_status": data.get("stage_status"),
                "started_at": data.get("started_at"),
            },
        })
    return {
        "value_numeric": len(sources),
        "currency": "",
        "unit": "count",
        "calculation": {"formula": METRICS["active_projects"]["formula"], "count": len(sources), "period_note": "current active projects"},
        "sources": sources,
    }

def _compute_alerts(conn, period_start, period_end, scope_type, scope_id) -> dict[str, Any]:
    where = ""
    params_extra: list[Any] = []
    if scope_type == "staff" and scope_id:
        where = " AND (a.staff_id = ? OR a.staff_id IS NULL) "
        params_extra.append(int(scope_id))
    elif scope_type == "project" and scope_id:
        where = " AND a.target_type = 'project' AND a.target_id = ? "
        params_extra.append(int(scope_id))
    elif scope_type == "kol" and scope_id:
        where = " AND a.target_type = 'kol' AND a.target_id = ? "
        params_extra.append(int(scope_id))
    rows = conn.execute(
        f"""
        SELECT a.id, a.alert_key, a.severity, a.status, a.target_type,
               a.target_id, a.staff_id, a.title, a.body, a.rule_key,
               a.due_at, a.created_at, a.updated_at
        FROM vkpi_alerts a
        WHERE COALESCE(a.status, 'open') = 'open'
        {where}
        ORDER BY
            CASE LOWER(COALESCE(a.severity, 'info'))
                WHEN 'critical' THEN 0
                WHEN 'high' THEN 1
                WHEN 'warning' THEN 2
                ELSE 3
            END,
            COALESCE(a.due_at, a.created_at) ASC
        LIMIT 1000
        """,
        tuple(params_extra),
    ).fetchall()
    sources = []
    for row in rows:
        data = dict(row)
        project_id = _int(data.get("target_id")) if str(data.get("target_type") or "").lower() == "project" else None
        kol_id = _int(data.get("target_id")) if str(data.get("target_type") or "").lower() == "kol" else None
        sources.append({
            "source_type": "alert",
            "source_id": _int(data.get("id")),
            "contribution_amount": 1,
            "evidence_type": str(data.get("severity") or "alert"),
            "evidence_ref": str(data.get("alert_key") or ""),
            "project_id": project_id or None,
            "kol_id": kol_id or None,
            "staff_id": _int(data.get("staff_id")) or None,
            "occurred_at": data.get("due_at") or data.get("created_at"),
            "snapshot": {
                "title": data.get("title"),
                "body": data.get("body"),
                "rule_key": data.get("rule_key"),
                "target_type": data.get("target_type"),
                "target_id": data.get("target_id"),
                "severity": data.get("severity"),
            },
        })
    return {
        "value_numeric": len(sources),
        "currency": "",
        "unit": "count",
        "calculation": {"formula": METRICS["alerts"]["formula"], "count": len(sources)},
        "sources": sources,
    }

def _compute_derived(metric_key: str, computed: dict[str, dict[str, Any]]) -> dict[str, Any]:
    gmv = _float(computed.get("gmv", {}).get("value_numeric"))
    cost = _float(computed.get("cost", {}).get("value_numeric"))
    if metric_key == "net_contribution":
        return {
            "value_numeric": gmv - cost,
            "currency": "USD",
            "unit": "cents",
            "calculation": {"formula": "gmv - cost", "gmv_cents": gmv, "cost_cents": cost},
            "sources": [],
        }
    if metric_key == "roi":
        roi = round(gmv / cost, 4) if cost else 0
        return {
            "value_numeric": roi,
            "currency": "",
            "unit": "ratio",
            "calculation": {"formula": "gmv / cost", "gmv_cents": gmv, "cost_cents": cost, "roi": roi},
            "sources": [],
        }
    raise ValueError(f"no derived handler for: {metric_key}")
