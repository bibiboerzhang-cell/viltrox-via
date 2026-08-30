"""Project detail read model for V-KPI workflow."""
from __future__ import annotations

import base64
import json
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains import business_truth
from app.domains.access import scope
from app.domains.projects import workflow_detail_assignments as assignment_stages
from app.domains.projects import workflow_detail_sections as detail_sections
from app.domains.projects.stage_canonical import stage_label_zh, to_canonical
from app.domains.projects.workflow_projects import _enrich_project_card_fields
from app.platform.db.schema import ensure_vkpi_schema
from app.platform.db.schema_audit import ensure_vkpi_audit_schema

logger = get_logger(__name__)


def _encode_assignment_cursor(project_id: int, row: dict[str, Any]) -> str:
    """Opaque, project-bound keyset cursor for the summary assignment feed."""
    payload = {
        "v": 1,
        "project_id": int(project_id),
        "stage_rank": int(row.get("assignment_stage_rank") or 9),
        "name": str(row.get("assignment_sort_name") or ""),
        "assignment_id": int(row.get("assignment_id") or 0),
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_assignment_cursor(project_id: int, cursor: str) -> tuple[int, str, int]:
    try:
        padded = str(cursor or "") + "=" * (-len(str(cursor or "")) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        if int(payload.get("v") or 0) != 1 or int(payload.get("project_id") or 0) != int(project_id):
            raise ValueError
        stage_rank = int(payload["stage_rank"])
        assignment_id = int(payload["assignment_id"])
        name = str(payload.get("name") or "")
        if stage_rank < 1 or stage_rank > 9 or assignment_id < 1:
            raise ValueError
        return stage_rank, name, assignment_id
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid assignment cursor") from exc


def _assignment_page_state(
    project_id: int,
    assignment_rows: list[dict[str, Any]],
    assignment_limit: int | None,
) -> tuple[list[dict[str, Any]], bool, str | None]:
    """Capture the next cursor before normalization removes its sort keys."""
    assignment_has_more = bool(
        assignment_limit is not None and len(assignment_rows) > assignment_limit
    )
    next_cursor = None
    if assignment_has_more and assignment_limit:
        next_cursor = _encode_assignment_cursor(project_id, assignment_rows[assignment_limit - 1])
    participating_kols, normalized_has_more = assignment_stages.prepare_assignment_page(
        assignment_rows,
        assignment_limit,
        to_canonical=to_canonical,
        stage_label_zh=stage_label_zh,
    )
    return participating_kols, normalized_has_more, next_cursor


def _load_assignments(
    conn: Any,
    project_id: int,
    *,
    assignment_limit: int | None,
    assignment_cursor: str | None,
) -> tuple[list[dict[str, Any]], bool, str | None]:
    cursor_values = None
    if assignment_limit is not None and assignment_cursor:
        cursor_values = _decode_assignment_cursor(project_id, assignment_cursor)
    assignment_rows = assignment_stages.fetch_assignment_rows(
        conn,
        project_id,
        assignment_limit=assignment_limit,
        cursor_values=cursor_values,
    )
    return _assignment_page_state(project_id, assignment_rows, assignment_limit)


def _load_audit_events(
    conn: Any,
    project_id: int,
    staff: dict[str, Any] | None,
    *,
    raw_costs: list[dict[str, Any]],
    content: dict[str, Any],
) -> list[dict[str, Any]]:
    if not scope.can_view_all(staff, domain="audit"):
        return []
    ensure_vkpi_audit_schema()
    return detail_sections.fetch_audit_events(
        conn,
        project_id,
        raw_costs=raw_costs,
        messages=content["messages"],
        content_posts=content["content_posts"],
        deliverables=content["deliverables"],
        shipments=content["shipments"],
        samples=content["samples"],
    )


def _base_result(
    *,
    project: dict[str, Any],
    events: list[dict[str, Any]],
    links: list[dict[str, Any]],
    link_clicks: list[dict[str, Any]],
    link_orders: list[dict[str, Any]],
    sales: list[dict[str, Any]],
    costs: list[dict[str, Any]],
    content: dict[str, Any],
    audit_events: list[dict[str, Any]],
    raw_costs: list[dict[str, Any]],
    show_financials: bool,
) -> dict[str, Any]:
    return {
        "project": project,
        "events": events,
        "links": links,
        "link_clicks": link_clicks,
        "link_orders": link_orders,
        "link_summary": detail_sections.build_link_summary(links, link_clicks, link_orders),
        "sales_attributions": sales,
        "costs": costs,
        "messages": content["messages"],
        "content_posts": content["content_posts"],
        "content_assets": content["content_assets"],
        "terms": content["terms"],
        "deliverables": content["deliverables"],
        "samples": content["samples"],
        "shipments": content["shipments"],
        "audit_events": audit_events,
        "roi": detail_sections.build_roi(
            sales,
            raw_costs,
            costs,
            show_financials=show_financials,
        ),
    }


def _attach_assignment_contract(
    result: dict[str, Any],
    project: dict[str, Any],
    participating_kols: list[dict[str, Any]],
    *,
    assignment_limit: int | None,
    assignment_has_more: bool,
    assignment_next_cursor: str | None,
) -> None:
    if assignment_limit is None:
        result["participating_kols"] = participating_kols
        result["project_kol_assignments"] = participating_kols
        return
    result["project_kol_assignments"] = participating_kols
    result["assignment_page"] = {
        "mode": "summary",
        "limit": int(assignment_limit),
        "count": len(participating_kols),
        "total": int(project.get("assignment_count") or 0),
        "has_more": assignment_has_more,
        "next_cursor": assignment_next_cursor,
    }


def project_detail(
    project_id: int,
    *,
    staff: dict[str, Any] | None = None,
    assignment_limit: int | None = None,
    assignment_cursor: str | None = None,
) -> dict[str, Any]:
    """Return full legacy detail, or a bounded assignment summary page."""
    if assignment_limit is not None:
        assignment_limit = max(1, min(int(assignment_limit), 100))
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff)
    conn = get_conn()
    row = assignment_stages.fetch_project_row(conn, project_id)
    if not row:
        raise LookupError("project not found")
    participating_kols, assignment_has_more, assignment_next_cursor = _load_assignments(
        conn,
        project_id,
        assignment_limit=assignment_limit,
        assignment_cursor=assignment_cursor,
    )
    project = dict(row)
    if assignment_limit is None:
        assignment_stages.apply_full_assignment_summary(project, participating_kols)
    _enrich_project_card_fields(conn, [project])

    events = detail_sections.fetch_events(conn, project_id)
    verified_attribution_predicate = business_truth.verified_shopify_attribution_sql("sa")
    links, link_clicks, link_orders = detail_sections.fetch_link_context(
        conn,
        project_id,
        verified_attribution_predicate=verified_attribution_predicate,
    )
    sales = detail_sections.fetch_sales_attributions(
        conn,
        project_id,
        verified_attribution_predicate=verified_attribution_predicate,
    )
    show_financials = scope.can_view_all(staff, domain="cost")
    raw_costs, costs = detail_sections.fetch_cost_context(
        conn,
        project_id,
        show_financials=show_financials,
        approved_actual_predicate=business_truth.approved_actual_cost_sql("c"),
    )
    content = detail_sections.fetch_content_context(conn, project_id)
    content["deliverables"] = detail_sections.resolve_deliverables(
        content["deliverables"],
        content["terms"],
        project_id=project_id,
        logger=logger,
    )
    detail_sections.redact_sample_costs(content["samples"], show_financials=show_financials)
    audit_events = _load_audit_events(
        conn,
        project_id,
        staff,
        raw_costs=raw_costs,
        content=content,
    )
    result = _base_result(
        project=project,
        events=events,
        links=links,
        link_clicks=link_clicks,
        link_orders=link_orders,
        sales=sales,
        costs=costs,
        content=content,
        audit_events=audit_events,
        raw_costs=raw_costs,
        show_financials=show_financials,
    )
    _attach_assignment_contract(
        result,
        project,
        participating_kols,
        assignment_limit=assignment_limit,
        assignment_has_more=assignment_has_more,
        assignment_next_cursor=assignment_next_cursor,
    )
    return result
