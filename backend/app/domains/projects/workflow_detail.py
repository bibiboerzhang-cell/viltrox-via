"""Project detail read model for V-KPI workflow."""
from __future__ import annotations

import json
from typing import Any

from app.db.connection import get_conn
from app.domains.access import scope
from app.platform.db.schema import ensure_vkpi_schema
from app.platform.db.schema_audit import ensure_vkpi_audit_schema
from app.domains.projects.workflow_common import _int

def project_detail(project_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff)
    conn = get_conn()
    row = conn.execute(
        """
        SELECT p.*,
               CASE
                   WHEN COALESCE(pa.kol_count, 0) > 1 THEN CAST(pa.kol_count AS TEXT) || ' KOL'
                   ELSE COALESCE(pk.display_name, '')
               END AS kol_name,
               CASE
                   WHEN COALESCE(pa.kol_count, 0) > 1 THEN 'multi'
                   ELSE COALESCE(pk.platform, '')
               END AS kol_platform,
               pk.handle AS handle,
               pk.avatar_url AS kol_avatar,
               pa.primary_kol_pool_id AS kol_pool_id,
               COALESCE(pa.kol_count, 0) AS kol_count,
               COALESCE(pa.kol_with_evidence, 0) AS kol_with_evidence,
               COALESCE(ev.evidence_count, 0) AS evidence_count,
               COALESCE(ev.evidence_kol_count, 0) AS evidence_kol_count,
               COALESCE(ev.total_views, 0) AS total_views,
               s.name AS staff_name
        FROM vkpi_projects p
        LEFT JOIN (
            SELECT
                a.project_id,
                COUNT(DISTINCT a.kol_pool_id) AS kol_count,
                COUNT(DISTINCT CASE WHEN kp.has_video_evidence THEN a.kol_pool_id END) AS kol_with_evidence,
                MIN(a.kol_pool_id) AS primary_kol_pool_id
            FROM vkpi_project_kol_assignments a
            LEFT JOIN vkpi_kol_pool kp ON kp.id = a.kol_pool_id
            WHERE a.project_id=?
            GROUP BY a.project_id
        ) pa ON pa.project_id = p.id
        LEFT JOIN vkpi_kol_pool pk ON pk.id = pa.primary_kol_pool_id
        LEFT JOIN (
            SELECT
                project_id,
                COUNT(*) AS evidence_count,
                COUNT(DISTINCT kol_pool_id) AS evidence_kol_count,
                COALESCE(SUM(COALESCE(view_count, 0)), 0) AS total_views
            FROM vkpi_kol_video_evidence
            WHERE project_id=?
            GROUP BY project_id
        ) ev ON ev.project_id = p.id
        LEFT JOIN staff st ON st.id = p.assigned_staff_id
        LEFT JOIN users s ON s.id = st.user_id
        WHERE p.id=?
        """,
        (int(project_id), int(project_id), int(project_id)),
    ).fetchone()
    if not row:
        raise LookupError("project not found")
    participating_kols = [
        dict(item)
        for item in conn.execute(
            """
            SELECT
                a.id AS assignment_id,
                a.project_id,
                a.kol_pool_id,
                a.stage,
                a.stage_status,
                a.assigned_staff_id,
                a.tracking_number,
                a.is_placeholder_tracking,
                a.source,
                a.source_ref,
                a.excel_progress,
                a.metadata_json,
                a.created_at,
                a.updated_at,
                kp.handle,
                kp.display_name AS kol_name,
                kp.display_name,
                kp.platform AS kol_platform,
                kp.platform,
                kp.profile_url,
                kp.avatar_url,
                kp.country,
                kp.followers,
                kp.dashboard_account_type,
                kp.dashboard_tier,
                kp.has_video_evidence,
                kp.video_evidence_count,
                COALESCE(ev.evidence_count, 0) AS evidence_count,
                COALESCE(ev.total_views, 0) AS total_views,
                COALESCE(ev.total_likes, 0) AS total_likes,
                COALESCE(ev.total_comments, 0) AS total_comments,
                ev.latest_publish_date
            FROM vkpi_project_kol_assignments a
            LEFT JOIN vkpi_kol_pool kp ON kp.id = a.kol_pool_id
            LEFT JOIN (
                SELECT
                    project_id,
                    kol_pool_id,
                    COUNT(*) AS evidence_count,
                    COALESCE(SUM(COALESCE(view_count, 0)), 0) AS total_views,
                    COALESCE(SUM(COALESCE(like_count, 0)), 0) AS total_likes,
                    COALESCE(SUM(COALESCE(comment_count, 0)), 0) AS total_comments,
                    MAX(publish_date) AS latest_publish_date
                FROM vkpi_kol_video_evidence
                WHERE project_id=?
                GROUP BY project_id, kol_pool_id
            ) ev ON ev.project_id = a.project_id AND ev.kol_pool_id = a.kol_pool_id
            WHERE a.project_id=?
            ORDER BY
                CASE a.stage
                    WHEN 'reviewed' THEN 1
                    WHEN 'content_posted' THEN 2
                    WHEN 'device_sent' THEN 3
                    WHEN 'agreed' THEN 4
                    WHEN 'replied' THEN 5
                    WHEN 'contacted' THEN 6
                    WHEN 'discovered' THEN 7
                    WHEN 'churned' THEN 8
                    ELSE 9
                END,
                kp.display_name ASC,
                a.id ASC
            """,
            (int(project_id), int(project_id)),
        ).fetchall()
    ]
    for item in participating_kols:
        for key in (
            "assignment_id",
            "project_id",
            "kol_pool_id",
            "assigned_staff_id",
            "followers",
            "video_evidence_count",
            "evidence_count",
            "total_views",
            "total_likes",
            "total_comments",
        ):
            item[key] = int(item.get(key) or 0)
        item["has_video_evidence"] = bool(item.get("has_video_evidence"))
    project = dict(row)
    if participating_kols:
        project["kol_count"] = len({int(item.get("kol_pool_id") or 0) for item in participating_kols if int(item.get("kol_pool_id") or 0)})
        project["kol_with_evidence"] = sum(1 for item in participating_kols if bool(item.get("has_video_evidence")))
        project["evidence_count"] = sum(int(item.get("evidence_count") or 0) for item in participating_kols)
        project["total_views"] = sum(int(item.get("total_views") or 0) for item in participating_kols)
        if project["kol_count"] > 1:
            project["kol_name"] = f"{project['kol_count']} KOL"
            project["kol_platform"] = "multi"
        else:
            only = participating_kols[0]
            project["kol_name"] = only.get("kol_name") or project.get("kol_name") or ""
            project["kol_platform"] = only.get("kol_platform") or project.get("kol_platform") or ""
    events = [
        dict(item)
        for item in conn.execute(
            "SELECT * FROM vkpi_project_stage_events WHERE project_id=? ORDER BY effective_at DESC, id DESC",
            (int(project_id),),
        ).fetchall()
    ]
    links = [
        dict(item)
        for item in conn.execute(
            "SELECT * FROM vkpi_links WHERE project_id=? ORDER BY created_at DESC, id DESC",
            (int(project_id),),
        ).fetchall()
    ]
    link_ids = [int(item.get("id") or 0) for item in links if int(item.get("id") or 0)]
    link_clicks: list[dict[str, Any]] = []
    link_orders: list[dict[str, Any]] = []
    if link_ids:
        placeholders = ",".join("?" for _ in link_ids)
        link_clicks = [
            dict(item)
            for item in conn.execute(
                f"""
                SELECT c.*, l.slug, l.destination_url AS link_destination_url
                FROM vkpi_link_clicks c
                LEFT JOIN vkpi_links l ON l.id = c.link_id
                WHERE c.link_id IN ({placeholders})
                ORDER BY c.clicked_at DESC, c.id DESC
                LIMIT 100
                """,
                link_ids,
            ).fetchall()
        ]
        link_orders = [
            dict(item)
            for item in conn.execute(
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
                WHERE sa.project_id=? AND sa.link_id IN ({placeholders})
                ORDER BY sa.occurred_at DESC, sa.id DESC
                LIMIT 100
                """,
                [int(project_id), *link_ids],
            ).fetchall()
        ]
    sales: list[dict[str, Any]] = []
    for item in conn.execute(
        """
        SELECT sa.*,
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
               os.discount_codes_json AS shopify_discount_codes_json,
               os.line_items_json AS shopify_line_items_json,
               os.raw_payload_hash AS shopify_raw_payload_hash
        FROM vkpi_sales_attributions sa
        LEFT JOIN vkpi_shopify_order_snapshots os ON os.id = sa.shopify_order_snapshot_id
        WHERE sa.project_id=?
        ORDER BY sa.occurred_at DESC, sa.id DESC
        """,
        (int(project_id),),
    ).fetchall():
        row_data = dict(item)
        if row_data.get("shopify_order_snapshot_id"):
            row_data["order_snapshot"] = {
                "id": row_data.get("shopify_order_snapshot_id"),
                "shopify_order_id": row_data.get("shopify_order_id"),
                "order_name": row_data.get("shopify_order_name"),
                "order_number": row_data.get("shopify_order_number"),
                "processed_at": row_data.get("shopify_processed_at"),
                "financial_status": row_data.get("shopify_financial_status"),
                "fulfillment_status": row_data.get("shopify_fulfillment_status"),
                "refund_status": row_data.get("shopify_refund_status"),
                "total_cents": row_data.get("shopify_total_cents"),
                "currency": row_data.get("shopify_currency"),
                "landing_site": row_data.get("shopify_landing_site"),
                "discount_codes_json": row_data.get("shopify_discount_codes_json"),
                "line_items_json": row_data.get("shopify_line_items_json"),
                "raw_payload_hash": row_data.get("shopify_raw_payload_hash"),
            }
        sales.append(row_data)
    show_financials = scope.can_view_all(staff, domain="cost")
    raw_costs = [
        dict(item)
        for item in conn.execute(
            "SELECT * FROM vkpi_cost_ledger WHERE project_id=? ORDER BY incurred_at DESC, id DESC",
            (int(project_id),),
        ).fetchall()
    ]
    if show_financials:
        costs = raw_costs
    else:
        # Operators can record shipping/cash fees, but internal lens/sample cost
        # and ROI basis must not be exposed through the detail API.
        costs = [item for item in raw_costs if str(item.get("cost_type") or "") in {"shipping", "cash_fee"}]
    messages = [
        dict(item)
        for item in conn.execute(
            "SELECT * FROM vkpi_messages WHERE project_id=? ORDER BY captured_at DESC, id DESC",
            (int(project_id),),
        ).fetchall()
    ]
    content_posts = [
        dict(item)
        for item in conn.execute(
            "SELECT * FROM vkpi_content_posts WHERE project_id=? ORDER BY published_at DESC, id DESC",
            (int(project_id),),
        ).fetchall()
    ]
    content_assets = [
        dict(item)
        for item in conn.execute(
            "SELECT * FROM vkpi_content_assets WHERE project_id=? ORDER BY created_at DESC, id DESC",
            (int(project_id),),
        ).fetchall()
    ]
    terms = conn.execute("SELECT * FROM vkpi_project_terms WHERE project_id=?", (int(project_id),)).fetchone()
    deliverables = [
        dict(item)
        for item in conn.execute(
            "SELECT * FROM vkpi_project_deliverables WHERE project_id=? ORDER BY due_at ASC, id ASC",
            (int(project_id),),
        ).fetchall()
    ]
    terms_dict = dict(terms) if terms else {}
    if not deliverables and terms_dict.get("deliverables_json"):
        try:
            raw_deliverables = json.loads(str(terms_dict.get("deliverables_json") or "[]"))
        except Exception:
            raw_deliverables = []
        if isinstance(raw_deliverables, list):
            deliverables = [
                {
                    **item,
                    "id": f"terms-{index + 1}",
                    "status": item.get("status") or "planned",
                    "source": "terms.deliverables_json",
                }
                for index, item in enumerate(raw_deliverables)
                if isinstance(item, dict)
            ]
    samples = [
        dict(item)
        for item in conn.execute(
            "SELECT * FROM vkpi_sample_assets WHERE project_id=? ORDER BY created_at DESC, id DESC",
            (int(project_id),),
        ).fetchall()
    ]
    if not show_financials:
        for sample in samples:
            sample["sample_cost_cents"] = None
    shipments = [
        dict(item)
        for item in conn.execute(
            "SELECT * FROM vkpi_shipments WHERE project_id=? ORDER BY created_at DESC, id DESC",
            (int(project_id),),
        ).fetchall()
    ]
    revenue_cents = sum(int(item.get("revenue_cents") or 0) for item in sales)
    cost_cents = sum(int(item.get("amount_cents") or 0) for item in raw_costs if str(item.get("status") or "") != "void")
    visible_cost_cents = sum(int(item.get("amount_cents") or 0) for item in costs if str(item.get("status") or "") != "void")
    link_summary = {
        "link_count": len(links),
        "click_count": sum(int(item.get("click_count") or 0) for item in links),
        "valid_click_count": sum(int(item.get("valid_click_count") or 0) for item in links),
        "bot_click_count": sum(int(item.get("bot_click_count") or 0) for item in links),
        "unique_click_count": sum(1 for item in link_clicks if int(item.get("is_unique") or 0)),
        "order_count": len({str(item.get("source_ref") or item.get("shopify_order_id") or item.get("attribution_id")) for item in link_orders}),
        "revenue_cents": sum(int(item.get("revenue_cents") or 0) for item in link_orders),
    }
    audit_events: list[dict[str, Any]] = []
    if scope.can_view_all(staff, domain="audit"):
        ensure_vkpi_audit_schema()

        def row_ids(rows: list[dict[str, Any]]) -> list[str]:
            return [str(_int(item.get("id"))) for item in rows if _int(item.get("id"))]

        audit_clauses: list[str] = ["(target_type=? AND target_id=?)"]
        audit_params: list[Any] = ["project", str(int(project_id))]

        def add_target_ids(target_type: str, ids: list[str]) -> None:
            clean = [item for item in ids if item and item != "0"]
            if not clean:
                return
            placeholders = ",".join("?" for _ in clean)
            audit_clauses.append(f"(target_type=? AND target_id IN ({placeholders}))")
            audit_params.extend([target_type, *clean])

        add_target_ids("cost", row_ids(raw_costs))
        add_target_ids("message", row_ids(messages))
        add_target_ids("content_post", row_ids(content_posts))
        add_target_ids("deliverable", row_ids([item for item in deliverables if isinstance(item.get("id"), int)]))
        add_target_ids("shipment", row_ids(shipments))
        add_target_ids("sample", row_ids(samples))
        audit_clauses.extend(["metadata_json LIKE ?", "metadata_json LIKE ?"])
        audit_params.extend([f'%"project_id": {int(project_id)}%', f'%"project_id":"{int(project_id)}"%'])
        audit_events = [
            dict(item)
            for item in conn.execute(
                f"""
                SELECT ba.*, s.name AS staff_name, u.email AS staff_email
                FROM vkpi_business_audit_logs ba
                LEFT JOIN staff st ON st.id = ba.staff_id
                LEFT JOIN users s ON s.id = st.user_id
                LEFT JOIN users u ON u.id = st.user_id
                WHERE {" OR ".join(audit_clauses)}
                ORDER BY ba.created_at DESC, ba.id DESC
                LIMIT 100
                """,
                audit_params,
            ).fetchall()
        ]
    return {
        "project": project,
        "participating_kols": participating_kols,
        "project_kol_assignments": participating_kols,
        "events": events,
        "links": links,
        "link_clicks": link_clicks,
        "link_orders": link_orders,
        "link_summary": link_summary,
        "sales_attributions": sales,
        "costs": costs,
        "messages": messages,
        "content_posts": content_posts,
        "content_assets": content_assets,
        "terms": terms_dict,
        "deliverables": deliverables,
        "samples": samples,
        "shipments": shipments,
        "audit_events": audit_events,
        "roi": {
            "revenue_cents": revenue_cents,
            "cost_cents": cost_cents if show_financials else visible_cost_cents if visible_cost_cents else None,
            "net_contribution_cents": revenue_cents - cost_cents if show_financials else None,
            "roi": round(revenue_cents / cost_cents, 4) if show_financials and cost_cents else None,
            "net_roi": round((revenue_cents - cost_cents) / cost_cents, 4) if show_financials and cost_cents else None,
            "financials_hidden": not show_financials,
        },
    }
