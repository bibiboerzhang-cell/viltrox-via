"""Entity resolution phases for KPI source-row evidence enrichment."""
from __future__ import annotations

from typing import Any


def _attach(
    context: dict[str, Any],
    entities: list[dict[str, Any]],
    key: str,
    entity_type: str,
    item: dict[str, Any],
    label_key: str,
    ops: dict[str, Any],
) -> None:
    if item:
        context[key] = item
        ops["_add_entity"](entities, entity_type, item, label_key)


def attach_project_and_kol(
    conn: Any,
    row: dict[str, Any],
    metadata: dict[str, Any],
    context: dict[str, Any],
    entities: list[dict[str, Any]],
    ops: dict[str, Any],
) -> None:
    project_id = ops["_int"](row.get("project_id") or metadata.get("project_id"))
    if project_id:
        project = ops["_row"](
            conn,
            "SELECT id, project_uid, project_name, stage, stage_status, product_sku, product_name, assigned_staff_id, updated_at FROM vkpi_projects WHERE id=?",
            (project_id,),
        )
        _attach(context, entities, "project", "project", project, "project_name", ops)
    kol_id = ops["_int"](row.get("kol_id") or metadata.get("kol_id"))
    if kol_id:
        kol = ops["_row"](
            conn,
            "SELECT id, channel_name, channel_url, platform, avatar_url, follower_count, avg_views FROM kols WHERE id=?",
            (kol_id,),
        )
        _attach(context, entities, "kol", "kol", kol, "channel_name", ops)


def attach_claim_stage_and_link(
    conn: Any,
    metadata: dict[str, Any],
    source_ref: Any,
    source_type: str,
    context: dict[str, Any],
    entities: list[dict[str, Any]],
    ops: dict[str, Any],
) -> None:
    claim_id = ops["_int"](metadata.get("claim_id")) or (
        source_type == "kol_claim" and ops["_source_ref_id"](source_ref, "claim")
    )
    if claim_id:
        claim = ops["_row"](
            conn,
            "SELECT id, kol_id, staff_id, project_id, status, claimed_at, released_at FROM vkpi_kol_claims WHERE id=?",
            (claim_id,),
        )
        _attach(context, entities, "claim", "claim", claim, "status", ops)
    stage_event_id = source_type == "project_stage_event" and ops["_source_ref_id"](source_ref, "stage")
    if stage_event_id:
        event = ops["_row"](
            conn,
            "SELECT id, project_id, from_stage, to_stage, event_type, actor_staff_id, source_ref_type, source_ref_id, effective_at FROM vkpi_project_stage_events WHERE id=?",
            (stage_event_id,),
        )
        _attach(context, entities, "stage_event", "stage_event", event, "to_stage", ops)
    link_id = ops["_int"](metadata.get("link_id")) or (
        source_type == "link" and ops["_source_ref_id"](source_ref, "link")
    )
    if link_id:
        link = ops["_row"](
            conn,
            "SELECT id, slug, destination_url, project_id, kol_id, staff_id, status, click_count, valid_click_count, bot_click_count FROM vkpi_links WHERE id=?",
            (link_id,),
        )
        _attach(context, entities, "link", "link", link, "slug", ops)


def attach_content_and_cost(
    conn: Any,
    metadata: dict[str, Any],
    source_ref: Any,
    source_type: str,
    context: dict[str, Any],
    entities: list[dict[str, Any]],
    ops: dict[str, Any],
) -> None:
    post_id = (
        ops["_int"](metadata.get("post_id"))
        or ops["_source_ref_id"](source_ref, "content")
        or ops["_source_ref_id"](source_ref, "content-views")
        or ops["_source_ref_id"](source_ref, "content-likes")
    )
    if post_id:
        post = ops["_row"](
            conn,
            "SELECT id, project_id, kol_id, link_id, platform, post_url, title, views, likes, comments, published_at FROM vkpi_content_posts WHERE id=?",
            (post_id,),
        )
        _attach(context, entities, "content_post", "content_post", post, "title", ops)
    cost_id = ops["_int"](metadata.get("cost_id")) or (
        source_type == "cost_ledger" and ops["_source_ref_id"](source_ref, "cost")
    )
    if cost_id:
        cost = ops["_row"](
            conn,
            "SELECT id, project_id, kol_id, staff_id, cost_type, amount_cents, currency, status, source_ref, incurred_at FROM vkpi_cost_ledger WHERE id=?",
            (cost_id,),
        )
        _attach(context, entities, "cost", "cost", cost, "cost_type", ops)


def attach_attribution(
    conn: Any,
    metadata: dict[str, Any],
    source_ref: Any,
    source_type: str,
    context: dict[str, Any],
    entities: list[dict[str, Any]],
    ops: dict[str, Any],
) -> None:
    attribution_id = ops["_int"](metadata.get("attribution_id")) or (
        source_type == "sales_attribution" and ops["_source_ref_id"](source_ref, "attribution")
    )
    if not attribution_id:
        return
    attribution = ops["_row"](
        conn,
        "SELECT id, source_platform, source_ref, project_id, link_id, kol_id, staff_id, shopify_order_snapshot_id, product_sku, revenue_cents, currency, confidence, occurred_at FROM vkpi_sales_attributions WHERE id=?",
        (attribution_id,),
    )
    if not attribution:
        return
    _attach(context, entities, "attribution", "attribution", attribution, "source_ref", ops)
    snapshot_id = ops["_int"](attribution.get("shopify_order_snapshot_id"))
    if snapshot_id:
        order = ops["_row"](
            conn,
            "SELECT id, shopify_order_id, order_name, order_number, processed_at, total_cents, currency, financial_status, refund_status FROM vkpi_shopify_order_snapshots WHERE id=?",
            (snapshot_id,),
        )
        _attach(context, entities, "shopify_order", "shopify_order", order, "order_name", ops)


def attach_recommendation_entities(
    conn: Any,
    metadata: dict[str, Any],
    context: dict[str, Any],
    entities: list[dict[str, Any]],
    ops: dict[str, Any],
) -> None:
    recommendation_id = ops["_int"](metadata.get("recommendation_id"))
    if recommendation_id:
        rec = ops["_row"](
            conn,
            "SELECT id, recommendation_uid, launch_id, kol_pool_id, linked_main_kol_id, platform, handle, display_name, score, rank, status FROM vkpi_kol_recommendations WHERE id=?",
            (recommendation_id,),
        )
        _attach(context, entities, "recommendation", "recommendation", rec, "recommendation_uid", ops)
    outcome_id = ops["_int"](metadata.get("outcome_id"))
    if outcome_id:
        outcome = ops["_row"](
            conn,
            "SELECT id, recommendation_id, launch_id, kol_pool_id, was_claimed, project_created, reply_received, agreement_reached, content_published, order_attributed, attributed_clicks, attributed_orders, attributed_gmv_cents, attributed_cost_cents, computed_roi, recommended_at, outcome_finalized_at FROM vkpi_recommendation_outcomes WHERE id=?",
            (outcome_id,),
        )
        _attach(
            context,
            entities,
            "recommendation_outcome",
            "recommendation_outcome",
            outcome,
            "recommendation_id",
            ops,
        )


def attach_launch_and_pool(
    conn: Any,
    metadata: dict[str, Any],
    context: dict[str, Any],
    entities: list[dict[str, Any]],
    ops: dict[str, Any],
) -> None:
    launch_id = ops["_int"](metadata.get("launch_id"))
    if launch_id:
        launch = ops["_row"](
            conn,
            "SELECT id, launch_uid, name, product_sku, product_name, status FROM vkpi_product_launches WHERE id=?",
            (launch_id,),
        )
        _attach(context, entities, "launch", "launch", launch, "name", ops)
    kol_pool_id = ops["_int"](metadata.get("kol_pool_id"))
    if kol_pool_id:
        pool = ops["_row"](
            conn,
            "SELECT id, pool_uid, platform, handle, display_name, followers, avg_views, engagement_rate, viltrox_fit_score, linked_main_kol_id FROM vkpi_kol_pool WHERE id=?",
            (kol_pool_id,),
        )
        _attach(context, entities, "kol_pool", "kol_pool", pool, "handle", ops)


def enrich_context(
    conn: Any,
    row: dict[str, Any],
    metadata: dict[str, Any],
    source_ref: Any,
    source_type: str,
    context: dict[str, Any],
    entities: list[dict[str, Any]],
    ops: dict[str, Any],
) -> None:
    attach_project_and_kol(conn, row, metadata, context, entities, ops)
    attach_claim_stage_and_link(conn, metadata, source_ref, source_type, context, entities, ops)
    attach_content_and_cost(conn, metadata, source_ref, source_type, context, entities, ops)
    attach_attribution(conn, metadata, source_ref, source_type, context, entities, ops)
    attach_recommendation_entities(conn, metadata, context, entities, ops)
    attach_launch_and_pool(conn, metadata, context, entities, ops)
