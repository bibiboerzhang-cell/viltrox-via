"""Recommendation outcome collector for self-learning prebuild."""
from __future__ import annotations

import json
from typing import Any

from app.db.connection import get_conn
from app.services.vkpi._utils import utcnow_iso
from app.services.vkpi.schema import ensure_vkpi_schema
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema

NODE_COLUMNS = {
    "shortlisted": ("was_shortlisted", "shortlisted_at"),
    "rejected": ("was_rejected", "rejected_at"),
    "claimed": ("was_claimed", "claimed_at"),
    "project_created": ("project_created", "project_created_at"),
    "outreach_sent": ("outreach_sent", "outreach_sent_at"),
    "reply_received": ("reply_received", "reply_at"),
    "agreement_reached": ("agreement_reached", "agreement_at"),
    "content_published": ("content_published", "content_published_at"),
    "order_attributed": ("order_attributed", "first_order_at"),
}


def _utcnow() -> str:
    return utcnow_iso()


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def ensure_outcome(recommendation_id: int, *, kol_pool_id: int | None = None, launch_id: int | None = None, feature_snapshot: dict[str, Any] | None = None, scoring_breakdown: dict[str, Any] | None = None, model_version: str = "rule_v0", display_position: int | None = None, display_context: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_recommendation_outcomes WHERE recommendation_id=?", (int(recommendation_id),)).fetchone()
    if not row:
        conn.execute(
            """
            INSERT INTO vkpi_recommendation_outcomes
                (recommendation_id, kol_pool_id, launch_id, recommended_at, feature_snapshot_json,
                 scoring_breakdown_json, model_version, display_position, display_context_json)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (int(recommendation_id), kol_pool_id, launch_id, _utcnow(), _json(feature_snapshot), _json(scoring_breakdown), model_version, display_position, _json(display_context)),
        )
        conn.commit()
    return get_outcome(recommendation_id)


def record(recommendation_id: int, node: str, *, context: dict[str, Any] | None = None, note: str = "") -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    if node not in NODE_COLUMNS:
        raise ValueError(f"unsupported outcome node: {node}")
    ensure_outcome(recommendation_id)
    bool_col, time_col = NODE_COLUMNS[node]
    now = _utcnow()
    extra = ""
    params: list[Any] = [True, now]
    if node == "rejected" and note:
        extra = ", reject_reason=?"
        params.append(note)
    if node == "content_published" and context and context.get("content_url"):
        extra += ", content_url=?"
        params.append(str(context.get("content_url") or ""))
    params.append(int(recommendation_id))
    get_conn().execute(f"UPDATE vkpi_recommendation_outcomes SET {bool_col}=?, {time_col}=?{extra}, first_action_at=COALESCE(first_action_at, ?) WHERE recommendation_id=?", [params[0], params[1], *params[2:-1], now, params[-1]])
    get_conn().commit()
    return get_outcome(recommendation_id)


def get_outcome(recommendation_id: int) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    row = get_conn().execute("SELECT * FROM vkpi_recommendation_outcomes WHERE recommendation_id=?", (int(recommendation_id),)).fetchone()
    return {"outcome": dict(row) if row else None}


def _ids_clause(column: str, values: list[int]) -> tuple[str, list[int]]:
    ids = [int(value) for value in values if int(value or 0) > 0]
    if not ids:
        return "1=0", []
    return f"{column} IN ({','.join('?' for _ in ids)})", ids


def _first_timestamp(values: list[Any]) -> str | None:
    stamps = [str(value) for value in values if value]
    return min(stamps) if stamps else None


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if not row:
        return default
    try:
        return row[key]
    except Exception:
        return default


def refresh_business_outcome(recommendation_id: int) -> dict[str, Any]:
    """Refresh outcome labels from real V-KPI business rows.

    This never fabricates platform data. It only promotes labels when matching
    project/message/content/link/sales/cost rows already exist.
    """

    ensure_vkpi_schema()
    ensure_vkpi_product_industry_schema()
    conn = get_conn()
    rec = conn.execute(
        "SELECT * FROM vkpi_kol_recommendations WHERE id=?",
        (int(recommendation_id),),
    ).fetchone()
    if not rec:
        return {"outcome": None, "aggregates": {"status": "recommendation_not_found"}}

    rec_dict = dict(rec)
    ensure_outcome(
        int(recommendation_id),
        kol_pool_id=rec_dict.get("kol_pool_id"),
        launch_id=rec_dict.get("launch_id"),
        feature_snapshot=_loads_safe(rec_dict.get("feature_snapshot_json")),
        scoring_breakdown=_loads_safe(rec_dict.get("scoring_breakdown_json")),
        model_version=str((_loads_safe(rec_dict.get("scoring_breakdown_json")) or {}).get("strategy_version") or "rule_v0"),
        display_position=rec_dict.get("rank"),
        display_context={"rank": rec_dict.get("rank"), "score": rec_dict.get("score"), "status": rec_dict.get("status")},
    )

    kol_id = int(rec_dict.get("linked_main_kol_id") or 0)
    rec_id = int(recommendation_id)
    projects = conn.execute(
        """
        SELECT id, stage, created_at, updated_at
        FROM vkpi_projects
        WHERE COALESCE(stage_status, '') != 'deleted'
          AND (
            metadata_json LIKE ?
            OR metadata_json LIKE ?
            OR (? > 0 AND kol_id=? AND source_type='product_recommendation')
          )
        """,
        (f'%"recommendation_id": {rec_id}%', f'%"recommendation_id":{rec_id}%', kol_id, kol_id),
    ).fetchall()
    project_ids = [int(row["id"]) for row in projects]
    project_clause, project_params = _ids_clause("project_id", project_ids)
    stage_map = {int(row["id"]): str(row["stage"] or "") for row in projects}

    message_where = []
    message_params: list[Any] = []
    if project_ids:
        clause, params = _ids_clause("project_id", project_ids)
        message_where.append(clause)
        message_params.extend(params)
    if kol_id:
        message_where.append("kol_id=?")
        message_params.append(kol_id)
    message_stats = None
    if message_where:
        message_stats = conn.execute(
            f"""
            SELECT
                MIN(captured_at) AS first_message_at,
                MIN(CASE WHEN direction='outbound' THEN captured_at END) AS first_outbound_at,
                MIN(CASE WHEN direction='inbound' THEN captured_at END) AS first_inbound_at
            FROM vkpi_messages
            WHERE {' OR '.join(f'({part})' for part in message_where)}
            """,
            tuple(message_params),
        ).fetchone()

    agreement_stage_at = None
    if project_ids:
        agreement_clause, agreement_params = _ids_clause("project_id", project_ids)
        agreement_stage_at = conn.execute(
            f"""
            SELECT MIN(effective_at) AS first_agreement_at
            FROM vkpi_project_stage_events
            WHERE {agreement_clause}
              AND to_stage IN ('agreed','shipped','received','published','measured','closed')
            """,
            tuple(agreement_params),
        ).fetchone()

    content_stats = None
    if project_ids:
        content_stats = conn.execute(
            f"""
            SELECT
                COUNT(*) AS n,
                MIN(COALESCE(published_at, created_at)) AS first_content_at,
                MIN(post_url) AS content_url
            FROM vkpi_content_posts
            WHERE {project_clause}
            """,
            tuple(project_params),
        ).fetchone()

    click_stats = None
    if project_ids or kol_id:
        click_where = []
        click_params: list[Any] = []
        if project_ids:
            link_project_clause, link_project_params = _ids_clause("l.project_id", project_ids)
            click_where.append(link_project_clause)
            click_params.extend(link_project_params)
        if kol_id:
            click_where.append("l.kol_id=?")
            click_params.append(kol_id)
        click_stats = conn.execute(
            f"""
            SELECT COUNT(*) AS valid_clicks
            FROM vkpi_link_clicks c
            JOIN vkpi_links l ON l.id = c.link_id
            WHERE COALESCE(c.is_bot, 0)=0
              AND ({' OR '.join(f'({part})' for part in click_where)})
            """,
            tuple(click_params),
        ).fetchone()

    sales_stats = None
    if project_ids or kol_id:
        sales_where = []
        sales_params: list[Any] = []
        if project_ids:
            sales_project_clause, sales_project_params = _ids_clause("project_id", project_ids)
            sales_where.append(sales_project_clause)
            sales_params.extend(sales_project_params)
        if kol_id:
            sales_where.append("kol_id=?")
            sales_params.append(kol_id)
        sales_stats = conn.execute(
            f"""
            SELECT
                COUNT(*) AS orders,
                COALESCE(SUM(revenue_cents), 0) AS gmv_cents,
                MIN(COALESCE(occurred_at, created_at)) AS first_order_at
            FROM vkpi_sales_attributions
            WHERE revenue_cents > 0
              AND confidence != 'excluded'
              AND ({' OR '.join(f'({part})' for part in sales_where)})
            """,
            tuple(sales_params),
        ).fetchone()

    cost_stats = None
    if project_ids:
        cost_stats = conn.execute(
            f"""
            SELECT COALESCE(SUM(amount_cents), 0) AS cost_cents
            FROM vkpi_cost_ledger
            WHERE {project_clause}
              AND status != 'void'
            """,
            tuple(project_params),
        ).fetchone()

    first_outreach = str(_row_get(message_stats, "first_outbound_at") or _row_get(message_stats, "first_message_at") or "") or None
    first_reply = str(_row_get(message_stats, "first_inbound_at") or "") or None
    first_agreement = str(_row_get(agreement_stage_at, "first_agreement_at") or "") or None
    if not first_agreement and any(stage in {"agreed", "shipped", "received", "published", "measured", "closed"} for stage in stage_map.values()):
        first_agreement = _first_timestamp([row["updated_at"] or row["created_at"] for row in projects])
    first_content = str(_row_get(content_stats, "first_content_at") or "") or None
    content_url = str(_row_get(content_stats, "content_url") or "") or ""
    first_order = str(_row_get(sales_stats, "first_order_at") or "") or None
    clicks = int(_row_get(click_stats, "valid_clicks", 0) or 0)
    orders = int(_row_get(sales_stats, "orders", 0) or 0)
    gmv_cents = int(_row_get(sales_stats, "gmv_cents", 0) or 0)
    cost_cents = int(_row_get(cost_stats, "cost_cents", 0) or 0)
    computed_roi = (float(gmv_cents) / float(cost_cents)) if cost_cents > 0 else None

    updates: list[str] = [
        "attributed_clicks=?",
        "attributed_orders=?",
        "attributed_gmv_cents=?",
        "attributed_cost_cents=?",
        "computed_roi=?",
    ]
    params: list[Any] = [clicks, orders, gmv_cents, cost_cents, computed_roi]
    first_actions: list[Any] = []
    if first_outreach:
        updates.extend(["outreach_sent=?", "outreach_sent_at=COALESCE(outreach_sent_at, ?)"])
        params.append(True)
        params.append(first_outreach)
        first_actions.append(first_outreach)
    if first_reply:
        updates.extend(["reply_received=?", "reply_at=COALESCE(reply_at, ?)", "reply_sentiment=COALESCE(NULLIF(reply_sentiment, ''), 'unknown')"])
        params.append(True)
        params.append(first_reply)
        first_actions.append(first_reply)
    if first_agreement:
        updates.extend(["agreement_reached=?", "agreement_at=COALESCE(agreement_at, ?)"])
        params.append(True)
        params.append(first_agreement)
        first_actions.append(first_agreement)
    if first_content:
        updates.extend(["content_published=?", "content_published_at=COALESCE(content_published_at, ?)"])
        params.append(True)
        params.append(first_content)
        first_actions.append(first_content)
        if content_url:
            updates.append("content_url=COALESCE(NULLIF(content_url, ''), ?)")
            params.append(content_url)
    if first_order and orders > 0:
        updates.extend(["order_attributed=?", "first_order_at=COALESCE(first_order_at, ?)"])
        params.append(True)
        params.append(first_order)
        first_actions.append(first_order)
    first_action = _first_timestamp(first_actions)
    if first_action:
        updates.append("first_action_at=COALESCE(first_action_at, ?)")
        params.append(first_action)
    params.append(rec_id)
    conn.execute(
        f"UPDATE vkpi_recommendation_outcomes SET {', '.join(updates)} WHERE recommendation_id=?",
        tuple(params),
    )
    conn.commit()
    result = get_outcome(rec_id)
    result["aggregates"] = {
        "project_ids": project_ids,
        "kol_id": kol_id,
        "outreach_sent": bool(first_outreach),
        "reply_received": bool(first_reply),
        "agreement_reached": bool(first_agreement),
        "content_published": bool(first_content),
        "order_attributed": orders > 0,
        "valid_clicks": clicks,
        "orders": orders,
        "gmv_cents": gmv_cents,
        "cost_cents": cost_cents,
        "computed_roi": computed_roi,
    }
    return result


def _loads_safe(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        loaded = json.loads(str(value or "{}"))
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}
