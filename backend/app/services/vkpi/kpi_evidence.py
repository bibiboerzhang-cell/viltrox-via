"""KPI source row evidence enrichment helpers."""
from __future__ import annotations

import json
from typing import Any


def _parse_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _row(conn: Any, sql: str, params: tuple[Any, ...]) -> dict[str, Any]:
    try:
        item = conn.execute(sql, params).fetchone()
        return dict(item) if item else {}
    except Exception:
        return {}


def _source_ref_id(source_ref: Any, prefix: str) -> int:
    raw = str(source_ref or "")
    marker = f"{prefix}:"
    if not raw.startswith(marker):
        return 0
    return _int(raw.split(marker, 1)[1].split(":", 1)[0])


def _add_entity(entities: list[dict[str, Any]], entity_type: str, item: dict[str, Any], label_key: str = "name") -> None:
    if not item:
        return
    entities.append(
        {
            "type": entity_type,
            "id": item.get("id"),
            "label": item.get(label_key) or item.get("project_name") or item.get("channel_name") or item.get("slug") or item.get("source_ref") or item.get("recommendation_uid") or item.get("order_name") or item.get("id"),
        }
    )


def enrich_kpi_source_row(conn: Any, source_row: dict[str, Any]) -> dict[str, Any]:
    """Attach source_context to one KPI ledger row.

    The ledger remains append-only; this only resolves human-readable context for
    staff/KOL evidence drawers and does not change metric values.
    """
    row = dict(source_row)
    metadata = _parse_json(row.get("metadata_json") or row.get("metadata"))
    source_ref = row.get("source_ref")
    source_type = str(row.get("source_type") or "")
    context: dict[str, Any] = {"entities": []}
    entities: list[dict[str, Any]] = context["entities"]

    project_id = _int(row.get("project_id") or metadata.get("project_id"))
    if project_id:
        project = _row(
            conn,
            "SELECT id, project_uid, project_name, stage, stage_status, product_sku, product_name, assigned_staff_id, updated_at FROM vkpi_projects WHERE id=?",
            (project_id,),
        )
        if project:
            context["project"] = project
            _add_entity(entities, "project", project, "project_name")

    kol_id = _int(row.get("kol_id") or metadata.get("kol_id"))
    if kol_id:
        kol = _row(
            conn,
            "SELECT id, channel_name, channel_url, platform, avatar_url, follower_count, avg_views FROM kols WHERE id=?",
            (kol_id,),
        )
        if kol:
            context["kol"] = kol
            _add_entity(entities, "kol", kol, "channel_name")

    claim_id = _int(metadata.get("claim_id")) or (source_type == "kol_claim" and _source_ref_id(source_ref, "claim"))
    if claim_id:
        claim = _row(conn, "SELECT id, kol_id, staff_id, project_id, status, claimed_at, released_at FROM vkpi_kol_claims WHERE id=?", (claim_id,))
        if claim:
            context["claim"] = claim
            _add_entity(entities, "claim", claim, "status")

    stage_event_id = source_type == "project_stage_event" and _source_ref_id(source_ref, "stage")
    if stage_event_id:
        event = _row(conn, "SELECT id, project_id, from_stage, to_stage, event_type, actor_staff_id, source_ref_type, source_ref_id, effective_at FROM vkpi_project_stage_events WHERE id=?", (stage_event_id,))
        if event:
            context["stage_event"] = event
            _add_entity(entities, "stage_event", event, "to_stage")

    link_id = _int(metadata.get("link_id")) or (source_type == "link" and _source_ref_id(source_ref, "link"))
    if link_id:
        link = _row(conn, "SELECT id, slug, destination_url, project_id, kol_id, staff_id, status, click_count, valid_click_count, bot_click_count FROM vkpi_links WHERE id=?", (link_id,))
        if link:
            context["link"] = link
            _add_entity(entities, "link", link, "slug")

    post_id = _int(metadata.get("post_id")) or _source_ref_id(source_ref, "content") or _source_ref_id(source_ref, "content-views") or _source_ref_id(source_ref, "content-likes")
    if post_id:
        post = _row(conn, "SELECT id, project_id, kol_id, link_id, platform, post_url, title, views, likes, comments, published_at FROM vkpi_content_posts WHERE id=?", (post_id,))
        if post:
            context["content_post"] = post
            _add_entity(entities, "content_post", post, "title")

    cost_id = _int(metadata.get("cost_id")) or (source_type == "cost_ledger" and _source_ref_id(source_ref, "cost"))
    if cost_id:
        cost = _row(conn, "SELECT id, project_id, kol_id, staff_id, cost_type, amount_cents, currency, status, source_ref, incurred_at FROM vkpi_cost_ledger WHERE id=?", (cost_id,))
        if cost:
            context["cost"] = cost
            _add_entity(entities, "cost", cost, "cost_type")

    attribution_id = _int(metadata.get("attribution_id")) or (source_type == "sales_attribution" and _source_ref_id(source_ref, "attribution"))
    if attribution_id:
        attribution = _row(
            conn,
            "SELECT id, source_platform, source_ref, project_id, link_id, kol_id, staff_id, shopify_order_snapshot_id, product_sku, revenue_cents, currency, confidence, occurred_at FROM vkpi_sales_attributions WHERE id=?",
            (attribution_id,),
        )
        if attribution:
            context["attribution"] = attribution
            _add_entity(entities, "attribution", attribution, "source_ref")
            snapshot_id = _int(attribution.get("shopify_order_snapshot_id"))
            if snapshot_id:
                order = _row(conn, "SELECT id, shopify_order_id, order_name, order_number, processed_at, total_cents, currency, financial_status, refund_status FROM vkpi_shopify_order_snapshots WHERE id=?", (snapshot_id,))
                if order:
                    context["shopify_order"] = order
                    _add_entity(entities, "shopify_order", order, "order_name")

    recommendation_id = _int(metadata.get("recommendation_id"))
    if recommendation_id:
        rec = _row(
            conn,
            "SELECT id, recommendation_uid, launch_id, kol_pool_id, linked_main_kol_id, platform, handle, display_name, score, rank, status FROM vkpi_kol_recommendations WHERE id=?",
            (recommendation_id,),
        )
        if rec:
            context["recommendation"] = rec
            _add_entity(entities, "recommendation", rec, "recommendation_uid")

    outcome_id = _int(metadata.get("outcome_id"))
    if outcome_id:
        outcome = _row(
            conn,
            "SELECT id, recommendation_id, launch_id, kol_pool_id, was_claimed, project_created, reply_received, agreement_reached, content_published, order_attributed, attributed_clicks, attributed_orders, attributed_gmv_cents, attributed_cost_cents, computed_roi, recommended_at, outcome_finalized_at FROM vkpi_recommendation_outcomes WHERE id=?",
            (outcome_id,),
        )
        if outcome:
            context["recommendation_outcome"] = outcome
            _add_entity(entities, "recommendation_outcome", outcome, "recommendation_id")

    launch_id = _int(metadata.get("launch_id"))
    if launch_id:
        launch = _row(conn, "SELECT id, launch_uid, name, product_sku, product_name, status FROM vkpi_product_launches WHERE id=?", (launch_id,))
        if launch:
            context["launch"] = launch
            _add_entity(entities, "launch", launch, "name")

    kol_pool_id = _int(metadata.get("kol_pool_id"))
    if kol_pool_id:
        pool = _row(conn, "SELECT id, pool_uid, platform, handle, display_name, followers, avg_views, engagement_rate, viltrox_fit_score, linked_main_kol_id FROM vkpi_kol_pool WHERE id=?", (kol_pool_id,))
        if pool:
            context["kol_pool"] = pool
            _add_entity(entities, "kol_pool", pool, "handle")

    if metadata.get("formula"):
        context["formula"] = metadata.get("formula")
        context["components"] = metadata.get("components") or []
    context["entity_count"] = len(entities)
    row["metadata"] = metadata
    row["source_context"] = context
    return row
