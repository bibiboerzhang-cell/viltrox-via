"""Commercial, content, and audit stages for the project detail read model."""
from __future__ import annotations

import json
from typing import Any


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def fetch_events(conn: Any, project_id: int) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in conn.execute(
            "SELECT * FROM vkpi_project_stage_events WHERE project_id=? ORDER BY effective_at DESC, id DESC",
            (int(project_id),),
        ).fetchall()
    ]


def fetch_link_context(
    conn: Any,
    project_id: int,
    *,
    verified_attribution_predicate: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    links = [
        dict(item)
        for item in conn.execute(
            "SELECT * FROM vkpi_links WHERE project_id=? ORDER BY created_at DESC, id DESC",
            (int(project_id),),
        ).fetchall()
    ]
    link_ids = [int(item.get("id") or 0) for item in links if int(item.get("id") or 0)]
    if not link_ids:
        return links, [], []
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
                   os.raw_payload_hash,
                   CASE WHEN {verified_attribution_predicate}
                        THEN 1 ELSE 0 END AS is_verified_business_truth
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
    for item in link_orders:
        item["business_truth_status"] = (
            "provider_verified"
            if int(item.get("is_verified_business_truth") or 0) == 1
            else "reference_only"
        )
    return links, link_clicks, link_orders


def fetch_sales_attributions(
    conn: Any,
    project_id: int,
    *,
    verified_attribution_predicate: str,
) -> list[dict[str, Any]]:
    sales: list[dict[str, Any]] = []
    rows = conn.execute(
        f"""
        SELECT sa.*,
               CASE WHEN {verified_attribution_predicate}
                    THEN 1 ELSE 0 END AS is_verified_business_truth,
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
    ).fetchall()
    for item in rows:
        row_data = dict(item)
        row_data["business_truth_status"] = (
            "provider_verified"
            if int(row_data.get("is_verified_business_truth") or 0) == 1
            else "reference_only"
        )
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
    return sales


def fetch_cost_context(
    conn: Any,
    project_id: int,
    *,
    show_financials: bool,
    approved_actual_predicate: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_costs = [
        dict(item)
        for item in conn.execute(
            f"""
            SELECT c.*,
                   CASE WHEN {approved_actual_predicate}
                        THEN 1 ELSE 0 END AS is_approved_actual
            FROM vkpi_cost_ledger c
            WHERE c.project_id=?
            ORDER BY c.incurred_at DESC, c.id DESC
            """,
            (int(project_id),),
        ).fetchall()
    ]
    for item in raw_costs:
        item["business_truth_status"] = (
            "approved_actual"
            if int(item.get("is_approved_actual") or 0) == 1
            else "reference_only"
        )
    if show_financials:
        return raw_costs, raw_costs
    visible_costs = [
        item
        for item in raw_costs
        if str(item.get("cost_type") or "") in {"shipping", "cash_fee"}
    ]
    return raw_costs, visible_costs


def fetch_content_context(conn: Any, project_id: int) -> dict[str, Any]:
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
    terms = conn.execute(
        "SELECT * FROM vkpi_project_terms WHERE project_id=?",
        (int(project_id),),
    ).fetchone()
    deliverables = [
        dict(item)
        for item in conn.execute(
            "SELECT * FROM vkpi_project_deliverables WHERE project_id=? ORDER BY due_at ASC, id ASC",
            (int(project_id),),
        ).fetchall()
    ]
    samples = [
        dict(item)
        for item in conn.execute(
            "SELECT * FROM vkpi_sample_assets WHERE project_id=? ORDER BY created_at DESC, id DESC",
            (int(project_id),),
        ).fetchall()
    ]
    shipments = [
        dict(item)
        for item in conn.execute(
            "SELECT * FROM vkpi_shipments WHERE project_id=? ORDER BY created_at DESC, id DESC",
            (int(project_id),),
        ).fetchall()
    ]
    return {
        "messages": messages,
        "content_posts": content_posts,
        "content_assets": content_assets,
        "terms": dict(terms) if terms else {},
        "deliverables": deliverables,
        "samples": samples,
        "shipments": shipments,
    }


def resolve_deliverables(
    deliverables: list[dict[str, Any]],
    terms_dict: dict[str, Any],
    *,
    project_id: int,
    logger: Any,
) -> list[dict[str, Any]]:
    """Use structured terms as the legacy fallback when rows are absent."""
    if deliverables or not terms_dict.get("deliverables_json"):
        return deliverables
    try:
        raw_deliverables = json.loads(str(terms_dict.get("deliverables_json") or "[]"))
    except Exception as exc:
        logger.warning(
            "vkpi.project_terms_deliverables_json_parse_failed",
            extra={"project_id": int(project_id), "error": str(exc)},
        )
        raw_deliverables = []
    if not isinstance(raw_deliverables, list):
        return deliverables
    return [
        {
            **item,
            "id": f"terms-{index + 1}",
            "status": item.get("status") or "planned",
            "source": "terms.deliverables_json",
        }
        for index, item in enumerate(raw_deliverables)
        if isinstance(item, dict)
    ]


def redact_sample_costs(samples: list[dict[str, Any]], *, show_financials: bool) -> None:
    if show_financials:
        return
    for sample in samples:
        sample["sample_cost_cents"] = None


def _row_ids(rows: list[dict[str, Any]]) -> list[str]:
    return [str(_int(item.get("id"))) for item in rows if _int(item.get("id"))]


def _add_audit_target_ids(
    clauses: list[str],
    params: list[Any],
    target_type: str,
    ids: list[str],
) -> None:
    clean = [item for item in ids if item and item != "0"]
    if not clean:
        return
    placeholders = ",".join("?" for _ in clean)
    clauses.append(f"(target_type=? AND target_id IN ({placeholders}))")
    params.extend([target_type, *clean])


def fetch_audit_events(
    conn: Any,
    project_id: int,
    *,
    raw_costs: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    content_posts: list[dict[str, Any]],
    deliverables: list[dict[str, Any]],
    shipments: list[dict[str, Any]],
    samples: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    audit_clauses: list[str] = ["(target_type=? AND target_id=?)"]
    audit_params: list[Any] = ["project", str(int(project_id))]
    _add_audit_target_ids(audit_clauses, audit_params, "cost", _row_ids(raw_costs))
    _add_audit_target_ids(audit_clauses, audit_params, "message", _row_ids(messages))
    _add_audit_target_ids(audit_clauses, audit_params, "content_post", _row_ids(content_posts))
    _add_audit_target_ids(
        audit_clauses,
        audit_params,
        "deliverable",
        _row_ids([item for item in deliverables if isinstance(item.get("id"), int)]),
    )
    _add_audit_target_ids(audit_clauses, audit_params, "shipment", _row_ids(shipments))
    _add_audit_target_ids(audit_clauses, audit_params, "sample", _row_ids(samples))
    audit_clauses.extend(["metadata_json LIKE ?", "metadata_json LIKE ?"])
    audit_params.extend(
        [f'%"project_id": {int(project_id)}%', f'%"project_id":"{int(project_id)}"%']
    )
    return [
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


def verified_rows(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    return [item for item in rows if int(item.get(field) or 0) == 1]


def build_link_summary(
    links: list[dict[str, Any]],
    link_clicks: list[dict[str, Any]],
    link_orders: list[dict[str, Any]],
) -> dict[str, int]:
    verified_link_orders = verified_rows(link_orders, "is_verified_business_truth")
    return {
        "link_count": len(links),
        "click_count": sum(int(item.get("click_count") or 0) for item in links),
        "valid_click_count": sum(int(item.get("valid_click_count") or 0) for item in links),
        "bot_click_count": sum(int(item.get("bot_click_count") or 0) for item in links),
        "unique_click_count": sum(1 for item in link_clicks if int(item.get("is_unique") or 0)),
        "order_count": len(
            {
                str(item.get("source_ref") or item.get("shopify_order_id") or item.get("attribution_id"))
                for item in verified_link_orders
            }
        ),
        "attribution_count": len(link_orders),
        "verified_attribution_count": len(verified_link_orders),
        "revenue_cents": sum(int(item.get("revenue_cents") or 0) for item in verified_link_orders),
    }


def build_roi(
    sales: list[dict[str, Any]],
    raw_costs: list[dict[str, Any]],
    costs: list[dict[str, Any]],
    *,
    show_financials: bool,
) -> dict[str, Any]:
    verified_sales = verified_rows(sales, "is_verified_business_truth")
    approved_costs = verified_rows(raw_costs, "is_approved_actual")
    visible_approved_costs = verified_rows(costs, "is_approved_actual")
    revenue_cents = sum(int(item.get("revenue_cents") or 0) for item in verified_sales)
    cost_cents = sum(int(item.get("amount_cents") or 0) for item in approved_costs)
    visible_cost_cents = sum(int(item.get("amount_cents") or 0) for item in visible_approved_costs)
    return {
        "revenue_cents": revenue_cents,
        "cost_cents": cost_cents if show_financials else visible_cost_cents if visible_cost_cents else None,
        "net_contribution_cents": revenue_cents - cost_cents if show_financials else None,
        "roi": round(revenue_cents / cost_cents, 4) if show_financials and cost_cents else None,
        "net_roi": round((revenue_cents - cost_cents) / cost_cents, 4)
        if show_financials and cost_cents
        else None,
        "financials_hidden": not show_financials,
    }
