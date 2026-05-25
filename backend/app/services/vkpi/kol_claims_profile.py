"""KOL profile dossier assembly for V-KPI."""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.domains.kol import profile_assembly
from app.domains.kol import profile_scope
from app.services.vkpi import scope
from app.services.vkpi.schema import ensure_vkpi_schema
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema
from app.services.vkpi.kol_claims_common import (
    _assert_kol_access,
    _int,
    _row_or_empty,
    _rows_or_empty,
    _safe_json_loads,
)

def profile(kol_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    ensure_vkpi_product_industry_schema()
    _assert_kol_access(kol_id, staff)
    conn = get_conn()
    row = conn.execute("SELECT * FROM kols WHERE id=?", (int(kol_id),)).fetchone()
    if not row:
        raise LookupError("kol not found")
    kol = dict(row)
    active_claim = _row_or_empty(
        """
        SELECT c.*, u.name AS staff_name, u.email AS staff_email, u.avatar_url AS staff_avatar_url
        FROM vkpi_kol_claims c
        LEFT JOIN staff st ON st.id = c.staff_id
        LEFT JOIN users u ON u.id = st.user_id
        WHERE c.kol_id=? AND c.status='active'
        ORDER BY c.claimed_at DESC, c.id DESC
        LIMIT 1
        """,
        (int(kol_id),),
    )
    staff_sql, staff_params = profile_scope.project_staff_filter(staff)
    params: list[Any] = [int(kol_id), *staff_params]
    projects = _rows_or_empty(
        f"""
        SELECT p.*, u.name AS staff_name, u.avatar_url AS staff_avatar_url
        FROM vkpi_projects p
        LEFT JOIN staff st ON st.id = p.assigned_staff_id
        LEFT JOIN users u ON u.id = st.user_id
        WHERE p.kol_id=? {staff_sql}
        ORDER BY p.updated_at DESC, p.id DESC
        LIMIT 100
        """,
        tuple(params),
    )
    is_manager = scope.can_view_all(staff)
    show_financials = scope.can_view_all(staff, domain="cost")
    actor = scope.actor_staff_id(staff)
    project_ids = [_int(item.get("id")) for item in projects if _int(item.get("id"))]

    def project_scope_clause(column: str) -> tuple[str, list[Any]]:
        return profile_scope.project_scope_clause(is_manager=is_manager, project_ids=project_ids, column=column)

    link_scope_sql, link_scope_params = profile_scope.link_scope_clause(
        is_manager=is_manager,
        actor=actor,
        project_ids=project_ids,
    )
    links = _rows_or_empty(
        f"""
        SELECT l.*, p.project_name
        FROM vkpi_links l
        LEFT JOIN vkpi_projects p ON p.id = l.project_id
        WHERE l.kol_id=? {link_scope_sql}
        ORDER BY l.updated_at DESC, l.id DESC
        LIMIT 100
        """,
        (int(kol_id), *link_scope_params),
    )
    link_ids = [_int(item.get("id")) for item in links if _int(item.get("id"))]
    link_clicks: list[dict[str, Any]] = []
    link_orders: list[dict[str, Any]] = []
    if link_ids:
        placeholders = ",".join("?" for _ in link_ids)
        link_clicks = _rows_or_empty(
            f"""
            SELECT c.*, l.slug, l.destination_url AS link_destination_url
            FROM vkpi_link_clicks c
            LEFT JOIN vkpi_links l ON l.id = c.link_id
            WHERE c.link_id IN ({placeholders})
            ORDER BY c.clicked_at DESC, c.id DESC
            LIMIT 100
            """,
            tuple(link_ids),
        )
        link_orders = _rows_or_empty(
            f"""
            SELECT sa.id AS attribution_id,
                   sa.source_platform,
                   sa.source_ref,
                   sa.link_id,
                   sa.project_id,
                   sa.kol_id,
                   sa.staff_id,
                   sa.revenue_cents,
                   sa.currency,
                   sa.confidence,
                   sa.occurred_at,
                   l.slug,
                   os.id AS shopify_order_snapshot_id,
                   os.shopify_order_id,
                   os.order_name,
                   os.order_number,
                   os.processed_at,
                   os.financial_status,
                   os.fulfillment_status,
                   os.refund_status,
                   os.total_cents,
                   os.landing_site,
                   os.raw_payload_hash
            FROM vkpi_sales_attributions sa
            LEFT JOIN vkpi_links l ON l.id = sa.link_id
            LEFT JOIN vkpi_shopify_order_snapshots os ON os.id = sa.shopify_order_snapshot_id
            WHERE sa.kol_id=? AND sa.link_id IN ({placeholders})
            ORDER BY sa.occurred_at DESC, sa.id DESC
            LIMIT 100
            """,
            (int(kol_id), *link_ids),
        )

    sale_scope_sql, sale_scope_params = project_scope_clause("sa.project_id")
    sales: list[dict[str, Any]] = []
    for item in _rows_or_empty(
        f"""
        SELECT sa.*, p.project_name,
               os.shopify_order_id,
               os.order_name AS shopify_order_name,
               os.order_number AS shopify_order_number,
               os.processed_at AS shopify_processed_at,
               os.financial_status AS shopify_financial_status,
               os.fulfillment_status AS shopify_fulfillment_status,
               os.refund_status AS shopify_refund_status,
               os.total_cents AS shopify_total_cents,
               os.currency AS shopify_currency,
               os.landing_site AS shopify_landing_site,
               os.raw_payload_hash AS shopify_raw_payload_hash
        FROM vkpi_sales_attributions sa
        LEFT JOIN vkpi_projects p ON p.id = sa.project_id
        LEFT JOIN vkpi_shopify_order_snapshots os ON os.id = sa.shopify_order_snapshot_id
        WHERE sa.kol_id=? {sale_scope_sql}
        ORDER BY sa.occurred_at DESC, sa.id DESC
        LIMIT 100
        """,
        (int(kol_id), *sale_scope_params),
    ):
        if item.get("shopify_order_snapshot_id"):
            item["order_snapshot"] = {
                "id": item.get("shopify_order_snapshot_id"),
                "shopify_order_id": item.get("shopify_order_id"),
                "order_name": item.get("shopify_order_name"),
                "order_number": item.get("shopify_order_number"),
                "processed_at": item.get("shopify_processed_at"),
                "financial_status": item.get("shopify_financial_status"),
                "fulfillment_status": item.get("shopify_fulfillment_status"),
                "refund_status": item.get("shopify_refund_status"),
                "total_cents": item.get("shopify_total_cents"),
                "currency": item.get("shopify_currency"),
                "landing_site": item.get("shopify_landing_site"),
                "raw_payload_hash": item.get("shopify_raw_payload_hash"),
            }
        sales.append(item)

    cost_scope_sql, cost_scope_params = project_scope_clause("cl.project_id")
    costs = _rows_or_empty(
        f"""
        SELECT cl.*, p.project_name
        FROM vkpi_cost_ledger cl
        LEFT JOIN vkpi_projects p ON p.id = cl.project_id
        WHERE cl.kol_id=? {cost_scope_sql}
        ORDER BY cl.incurred_at DESC, cl.id DESC
        LIMIT 100
        """,
        (int(kol_id), *cost_scope_params),
    ) if show_financials else []

    kpi_scope_sql, kpi_scope_params = project_scope_clause("kl.project_id")
    kpi_ledger = _rows_or_empty(
        f"""
        SELECT kl.*, p.project_name, u.name AS staff_name
        FROM vkpi_kpi_ledger kl
        LEFT JOIN vkpi_projects p ON p.id = kl.project_id
        LEFT JOIN staff st ON st.id = kl.staff_id
        LEFT JOIN users u ON u.id = st.user_id
        WHERE kl.kol_id=? {kpi_scope_sql}
        ORDER BY kl.ledger_date DESC, kl.id DESC
        LIMIT 100
        """,
        (int(kol_id), *kpi_scope_params),
    )
    kpi_grouped: dict[str, dict[str, Any]] = {}
    for item in kpi_ledger:
        metric_key = str(item.get("metric_key") or "unknown")
        bucket = kpi_grouped.setdefault(
            metric_key,
            {
                "metric_key": metric_key,
                "total_value": 0.0,
                "row_count": 0,
                "latest_ledger_date": "",
                "latest_source_ref": "",
            },
        )
        try:
            bucket["total_value"] = float(bucket["total_value"]) + float(item.get("metric_value") or 0)
        except (TypeError, ValueError):
            pass
        bucket["row_count"] = int(bucket["row_count"]) + 1
        ledger_date = str(item.get("ledger_date") or "")
        if ledger_date >= str(bucket.get("latest_ledger_date") or ""):
            bucket["latest_ledger_date"] = ledger_date
            bucket["latest_source_ref"] = item.get("source_ref") or ""
    kpi_summary = sorted(kpi_grouped.values(), key=lambda item: str(item.get("metric_key") or ""))

    recommendations = _rows_or_empty(
        """
        SELECT r.*,
               run.run_uid,
               run.strategy_version,
               kp.pool_uid,
               kp.viltrox_fit_score,
               kp.source_type AS pool_source_type,
               kp.source_ref AS pool_source_ref
        FROM vkpi_kol_recommendations r
        LEFT JOIN vkpi_kol_recommendation_runs run ON run.id = r.run_id
        LEFT JOIN vkpi_kol_pool kp ON kp.id = r.kol_pool_id
        WHERE r.linked_main_kol_id=? OR kp.linked_main_kol_id=?
        ORDER BY r.created_at DESC, r.id DESC
        LIMIT 100
        """,
        (int(kol_id), int(kol_id)),
    ) if is_manager else []
    recommendation_outcomes = _rows_or_empty(
        """
        SELECT o.*,
               r.recommendation_uid,
               r.score,
               r.rank,
               r.status AS recommendation_status,
               r.platform,
               r.handle,
               r.display_name,
               run.run_uid,
               run.strategy_version,
               kp.pool_uid,
               kp.source_type AS pool_source_type,
               kp.source_ref AS pool_source_ref
        FROM vkpi_recommendation_outcomes o
        LEFT JOIN vkpi_kol_recommendations r ON r.id = o.recommendation_id
        LEFT JOIN vkpi_kol_recommendation_runs run ON run.id = r.run_id
        LEFT JOIN vkpi_kol_pool kp ON kp.id = COALESCE(o.kol_pool_id, r.kol_pool_id)
        WHERE r.linked_main_kol_id=? OR kp.linked_main_kol_id=?
        ORDER BY o.recommended_at DESC, o.id DESC
        LIMIT 100
        """,
        (int(kol_id), int(kol_id)),
    ) if is_manager else []
    audit_events: list[dict[str, Any]] = []
    if is_manager:
        audit_where, audit_params = profile_scope.audit_where_parts(
            kol_id=int(kol_id),
            project_ids=project_ids,
            link_ids=link_ids,
            sales=sales,
            costs=costs,
        )
        audit_events = _rows_or_empty(
            f"""
            SELECT id, staff_id, action_type, target_type, target_id, detail, metadata_json, created_at
            FROM vkpi_business_audit_logs
            WHERE {audit_where}
            ORDER BY created_at DESC, id DESC
            LIMIT 100
            """,
            tuple(audit_params),
        )

    message_scope_sql, message_scope_params = project_scope_clause("m.project_id")
    messages = _rows_or_empty(
        f"""
        SELECT m.*, p.project_name
        FROM vkpi_messages m
        LEFT JOIN vkpi_projects p ON p.id = m.project_id
        WHERE m.kol_id=? {message_scope_sql}
        ORDER BY m.captured_at DESC, m.id DESC
        LIMIT 100
        """,
        (int(kol_id), *message_scope_params),
    )
    content_scope_sql, content_scope_params = project_scope_clause("cp.project_id")
    content_posts = _rows_or_empty(
        f"""
        SELECT cp.*, p.project_name
        FROM vkpi_content_posts cp
        LEFT JOIN vkpi_projects p ON p.id = cp.project_id
        WHERE cp.kol_id=? {content_scope_sql}
        ORDER BY cp.published_at DESC, cp.id DESC
        LIMIT 100
        """,
        (int(kol_id), *content_scope_params),
    )
    post_ids = [_int(item.get("id")) for item in content_posts if _int(item.get("id"))]
    if post_ids:
        placeholders = ",".join("?" for _ in post_ids)
        content_assets = _rows_or_empty(
            f"""
            SELECT *
            FROM vkpi_content_assets
            WHERE post_id IN ({placeholders})
            ORDER BY created_at DESC, id DESC
            LIMIT 100
            """,
            tuple(post_ids),
        )
    else:
        content_assets = []
    claim_history = _rows_or_empty(
        """
        SELECT c.*, u.name AS staff_name, u.email AS staff_email
        FROM vkpi_kol_claims c
        LEFT JOIN staff st ON st.id = c.staff_id
        LEFT JOIN users u ON u.id = st.user_id
        WHERE c.kol_id=?
        ORDER BY c.claimed_at DESC, c.id DESC
        LIMIT 100
        """,
        (int(kol_id),),
    )
    snapshot = _row_or_empty(
        "SELECT * FROM kol_account_snapshots WHERE kol_id=? ORDER BY scanned_at DESC, id DESC LIMIT 1",
        (int(kol_id),),
    )
    posts = _rows_or_empty(
        """
        SELECT *
        FROM kol_posts
        WHERE kol_id=?
        ORDER BY views DESC, published_at DESC, id DESC
        LIMIT 20
        """,
        (int(kol_id),),
    )
    report = _row_or_empty(
        """
        SELECT *
        FROM kol_analysis_reports
        WHERE kol_id=?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (int(kol_id),),
    )
    raw_report = _safe_json_loads(report.get("raw_json"), {}) if report else {}
    revenue_cents = sum(_int(item.get("revenue_cents")) for item in sales)
    cost_cents = sum(_int(item.get("amount_cents")) for item in costs if str(item.get("status") or "") != "void")
    contact_links = _safe_json_loads(kol.get("contact_links_json"), [])
    contact_raw = _safe_json_loads(kol.get("contact_raw_json"), {})
    activity_timeline = profile_assembly.build_activity_timeline(
        claim_history=claim_history,
        projects=projects,
        messages=messages,
        content_posts=content_posts,
        sales=sales,
        kpi_ledger=kpi_ledger,
        recommendation_outcomes=recommendation_outcomes,
        link_clicks=link_clicks,
        audit_events=audit_events,
    )
    link_summary = profile_assembly.build_link_summary(links, link_clicks, link_orders)
    return {
        "kol": kol,
        "active_claim": active_claim,
        "claim_history": claim_history,
        "projects": projects,
        "links": links,
        "link_clicks": link_clicks,
        "link_orders": link_orders,
        "link_summary": link_summary,
        "sales_attributions": sales,
        "costs": costs,
        "messages": messages,
        "content_posts": content_posts,
        "content_assets": content_assets,
        "kpi_ledger": kpi_ledger,
        "kpi_summary": kpi_summary,
        "recommendations": recommendations,
        "recommendation_outcomes": recommendation_outcomes,
        "audit_events": audit_events,
        "contacts": profile_assembly.build_contacts(kol, contact_links, contact_raw),
        "activity_timeline": activity_timeline,
        "snapshot": snapshot,
        "posts": posts,
        "analysis_report": report,
        "summary": profile_assembly.build_profile_summary(
            snapshot=snapshot,
            kol=kol,
            posts=posts,
            report=report,
            raw_report=raw_report,
            revenue_cents=revenue_cents,
            cost_cents=cost_cents,
            show_financials=show_financials,
            projects=projects,
            links=links,
            link_clicks=link_clicks,
            link_orders=link_orders,
            messages=messages,
            content_posts=content_posts,
            claim_history=claim_history,
            kpi_ledger=kpi_ledger,
            kpi_summary=kpi_summary,
            recommendations=recommendations,
            recommendation_outcomes=recommendation_outcomes,
        ),
    }
