"""Evidence and outcome read models for Product Analysis recommendations."""
from __future__ import annotations

import json
import os
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime
from app.domains.kol import pool as kol_pool
from app.domains.recommendations import outcomes as outcome_collector
from app.services.vkpi import audit
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema
from app.services.vkpi.workflow import staff_id as resolve_staff_id

logger = get_logger(__name__)


def _loads(value: Any, default: Any = None) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception as exc:
        logger.warning("vkpi product analysis evidence json parse failed: %s", exc)
        return default


def _postgres_selected() -> bool:
    return os.environ.get("DB_RUNTIME_BACKEND") == "postgres" or is_postgres_runtime()


def _bool_count_expr(column: str) -> str:
    """Return a portable SQL expression for counting true-ish boolean columns."""
    if _postgres_selected():
        return f"CASE WHEN {column} IS TRUE THEN 1 ELSE 0 END"
    return f"CASE WHEN COALESCE({column}, 0)=1 THEN 1 ELSE 0 END"


def _recommendation_context(recommendation_id: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_kol_recommendations WHERE id=?", (int(recommendation_id),)).fetchone()
    if not row:
        raise LookupError("recommendation not found")
    rec = dict(row)
    pool: dict[str, Any] = {}
    if rec.get("kol_pool_id"):
        pool = kol_pool.get_item(int(rec["kol_pool_id"])).get("item") or {}
    launch: dict[str, Any] = {}
    if rec.get("launch_id"):
        launch_row = conn.execute("SELECT * FROM vkpi_product_launches WHERE id=?", (int(rec["launch_id"]),)).fetchone()
        launch = dict(launch_row) if launch_row else {}
    return rec, pool, launch


def _safe_rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in get_conn().execute(sql, params).fetchall()]
    except Exception as exc:
        logger.warning("vkpi product analysis evidence rows query failed: %s", exc)
        return []


def _safe_row(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    try:
        row = get_conn().execute(sql, params).fetchone()
        return dict(row) if row else {}
    except Exception as exc:
        logger.warning("vkpi product analysis evidence row query failed: %s", exc)
        return {}


def _source_row(source_type: str, source_id: Any, label: str, row: dict[str, Any], evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "source_type": source_type,
        "source_id": str(source_id or ""),
        "label": label,
        "evidence": evidence or {},
        "row": row,
    }


def get_recommendation_evidence(recommendation_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the frozen feature snapshot plus real downstream business evidence.

    This endpoint is intentionally evidence-first: it refreshes outcome labels
    only from rows already present in V-KPI and never invents platform stats.
    """

    ensure_vkpi_product_industry_schema()
    rec, pool, launch = _recommendation_context(int(recommendation_id))
    refreshed = outcome_collector.refresh_business_outcome(int(recommendation_id))
    outcome = (refreshed.get("outcome") if isinstance(refreshed, dict) else None) or outcome_collector.get_outcome(int(recommendation_id)).get("outcome")
    conn = get_conn()

    explanations = _safe_rows(
        """
        SELECT *
        FROM vkpi_recommendation_explanations
        WHERE recommendation_id=?
        ORDER BY created_at DESC, id DESC
        LIMIT 20
        """,
        (int(recommendation_id),),
    )
    feedback = _safe_rows(
        """
        SELECT *
        FROM vkpi_recommendation_feedback
        WHERE recommendation_id=?
        ORDER BY created_at DESC, id DESC
        LIMIT 50
        """,
        (int(recommendation_id),),
    )
    assignments = _safe_rows(
        """
        SELECT *
        FROM vkpi_recommendation_assignments
        WHERE recommendation_id=?
        ORDER BY assigned_at DESC, id DESC
        LIMIT 10
        """,
        (int(recommendation_id),),
    )
    run = _safe_row("SELECT * FROM vkpi_kol_recommendation_runs WHERE id=?", (int(rec.get("run_id") or 0),))

    kol_id = int(rec.get("linked_main_kol_id") or 0)
    project_rows = _safe_rows(
        """
        SELECT *
        FROM vkpi_projects
        WHERE COALESCE(stage_status, '') != 'deleted'
          AND (
            metadata_json LIKE ?
            OR metadata_json LIKE ?
            OR (? > 0 AND kol_id=?)
          )
        ORDER BY updated_at DESC, id DESC
        LIMIT 50
        """,
        (f'%"recommendation_id": {int(recommendation_id)}%', f'%"recommendation_id":{int(recommendation_id)}%', kol_id, kol_id),
    )
    project_ids = [int(row.get("id") or 0) for row in project_rows if int(row.get("id") or 0) > 0]
    project_placeholders = ",".join("?" for _ in project_ids)

    if project_ids:
        links = _safe_rows(f"SELECT * FROM vkpi_links WHERE project_id IN ({project_placeholders}) ORDER BY created_at DESC, id DESC LIMIT 50", tuple(project_ids))
        messages = _safe_rows(f"SELECT * FROM vkpi_messages WHERE project_id IN ({project_placeholders}) ORDER BY captured_at DESC, id DESC LIMIT 50", tuple(project_ids))
        content = _safe_rows(f"SELECT * FROM vkpi_content_posts WHERE project_id IN ({project_placeholders}) ORDER BY COALESCE(published_at, created_at) DESC, id DESC LIMIT 50", tuple(project_ids))
        costs = _safe_rows(f"SELECT * FROM vkpi_cost_ledger WHERE project_id IN ({project_placeholders}) ORDER BY incurred_at DESC, id DESC LIMIT 50", tuple(project_ids))
        attribution = _safe_rows(f"SELECT * FROM vkpi_sales_attributions WHERE project_id IN ({project_placeholders}) ORDER BY COALESCE(occurred_at, imported_at, created_at) DESC, id DESC LIMIT 50", tuple(project_ids))
        terms = _safe_rows(f"SELECT * FROM vkpi_project_terms WHERE project_id IN ({project_placeholders}) ORDER BY updated_at DESC, id DESC LIMIT 20", tuple(project_ids))
        deliverables = _safe_rows(f"SELECT * FROM vkpi_project_deliverables WHERE project_id IN ({project_placeholders}) ORDER BY updated_at DESC, id DESC LIMIT 50", tuple(project_ids))
        samples = _safe_rows(f"SELECT * FROM vkpi_sample_assets WHERE project_id IN ({project_placeholders}) ORDER BY updated_at DESC, id DESC LIMIT 50", tuple(project_ids))
        shipments = _safe_rows(f"SELECT * FROM vkpi_shipments WHERE project_id IN ({project_placeholders}) ORDER BY updated_at DESC, id DESC LIMIT 50", tuple(project_ids))
    else:
        links, messages, content, costs, attribution, terms, deliverables, samples, shipments = [], [], [], [], [], [], [], [], []

    shopify_snapshot_ids = [int(row.get("shopify_order_snapshot_id") or 0) for row in attribution if int(row.get("shopify_order_snapshot_id") or 0) > 0]
    if shopify_snapshot_ids:
        placeholders = ",".join("?" for _ in shopify_snapshot_ids)
        shopify_orders = _safe_rows(f"SELECT * FROM vkpi_shopify_order_snapshots WHERE id IN ({placeholders}) ORDER BY processed_at DESC, id DESC LIMIT 50", tuple(shopify_snapshot_ids))
    else:
        shopify_orders = []

    audit_rows = _safe_rows(
        """
        SELECT *
        FROM vkpi_business_audit_logs
        WHERE (target_type='recommendation' AND target_id=?)
           OR (target_type='product_launch' AND target_id=?)
        ORDER BY created_at DESC, id DESC
        LIMIT 50
        """,
        (str(recommendation_id), str(rec.get("launch_id") or "")),
    )

    feature_snapshot = _loads(rec.get("feature_snapshot_json"), {}) or _loads((outcome or {}).get("feature_snapshot_json"), {}) or {}
    scoring_breakdown = _loads(rec.get("scoring_breakdown_json"), {}) or _loads((outcome or {}).get("scoring_breakdown_json"), {}) or {}
    explanation = _loads(rec.get("explanation_json"), {}) or {}

    source_rows: list[dict[str, Any]] = [
        _source_row("recommendation", rec.get("id"), "推荐候选", rec, {"rank": rec.get("rank"), "score": rec.get("score")}),
        _source_row("feature_snapshot", rec.get("id"), "推荐时刻冻结特征", feature_snapshot, {"frozen": True}),
        _source_row("scoring_breakdown", rec.get("id"), "评分明细", scoring_breakdown, {"model_version": scoring_breakdown.get("strategy_version") or (outcome or {}).get("model_version") or "rule_v0"}),
    ]
    if outcome:
        source_rows.append(_source_row("outcome", outcome.get("id"), "Outcome 标签", outcome, {"recommendation_id": recommendation_id}))
    if run:
        source_rows.append(_source_row("recommendation_run", run.get("id"), "推荐运行", run, {"strategy_version": run.get("strategy_version")}))
    if launch:
        source_rows.append(_source_row("product_launch", launch.get("id"), "产品发布项目", launch, {"launch_id": launch.get("id")}))
    if pool:
        source_rows.append(_source_row("kol_pool", pool.get("id"), "KOL 池账号", pool, {"platform": pool.get("platform")}))
    for row in explanations[:10]:
        source_rows.append(_source_row("explanation", row.get("id"), "解释记录", row, {"model_version": row.get("model_version")}))
    for row in feedback[:10]:
        source_rows.append(_source_row("feedback", row.get("id"), "员工反馈", row, {"feedback_type": row.get("feedback_type")}))
    for row in assignments[:10]:
        source_rows.append(_source_row("assignment", row.get("id"), "AB/策略分流", row, {"variant": row.get("variant")}))
    for source_type, label, rows in (
        ("project", "项目", project_rows),
        ("message", "消息证据", messages),
        ("terms", "合作条款", terms),
        ("deliverable", "交付物", deliverables),
        ("sample", "样品", samples),
        ("shipment", "物流", shipments),
        ("content", "内容发布", content),
        ("link", "短链", links),
        ("attribution", "销售归因", attribution),
        ("shopify_order", "Shopify 订单快照", shopify_orders),
        ("cost", "成本", costs),
        ("audit", "业务审计", audit_rows),
    ):
        for row in rows[:20]:
            source_rows.append(_source_row(source_type, row.get("id"), label, row))

    audit.log_business_event(
        staff_id=resolve_staff_id(staff) or 0,
        action_type="recommendation_evidence_view",
        target_type="recommendation",
        target_id=int(recommendation_id),
        metadata={"source_count": len(source_rows)},
    )
    return {
        "recommendation": rec,
        "launch": launch,
        "kol_pool": pool,
        "run": run,
        "outcome": outcome,
        "aggregates": refreshed.get("aggregates") if isinstance(refreshed, dict) else {},
        "feature_snapshot": feature_snapshot,
        "scoring_breakdown": scoring_breakdown,
        "explanation": explanation,
        "explanations": explanations,
        "feedback": feedback,
        "assignments": assignments,
        "projects": project_rows,
        "messages": messages,
        "terms": terms,
        "deliverables": deliverables,
        "samples": samples,
        "shipments": shipments,
        "content": content,
        "links": links,
        "attribution": attribution,
        "shopify_orders": shopify_orders,
        "costs": costs,
        "audit": audit_rows,
        "source_rows": source_rows,
        "source_count": len(source_rows),
        "evidence": {
            "recommendation_id": int(recommendation_id),
            "frozen_at": (outcome or {}).get("recommended_at") or rec.get("created_at"),
            "model_version": (outcome or {}).get("model_version") or scoring_breakdown.get("strategy_version") or "rule_v0",
            "display_position": (outcome or {}).get("display_position") or rec.get("rank"),
            "source_count": len(source_rows),
            "no_fake_platform_stats": True,
        },
    }


def recommendation_outcome_summary(launch_id: int | None = None, run_id: int | None = None, limit: int = 50) -> dict[str, Any]:
    """Return real outcome conversion for Product Analysis recommendations."""
    ensure_vkpi_product_industry_schema()
    where: list[str] = []
    params: list[Any] = []
    if launch_id:
        where.append("r.launch_id=?")
        params.append(int(launch_id))
    if run_id:
        where.append("r.run_id=?")
        params.append(int(run_id))
    clause = "WHERE " + " AND ".join(where) if where else ""
    conn = get_conn()
    totals = dict(conn.execute(
        f"""
        SELECT
            COUNT(*) AS recommendations,
            COALESCE(SUM({_bool_count_expr("o.was_shortlisted")}), 0) AS shortlisted,
            COALESCE(SUM({_bool_count_expr("o.was_rejected")}), 0) AS rejected,
            COALESCE(SUM({_bool_count_expr("o.was_claimed")}), 0) AS claimed,
            COALESCE(SUM({_bool_count_expr("o.project_created")}), 0) AS project_created,
            COALESCE(SUM({_bool_count_expr("o.outreach_sent")}), 0) AS outreach_sent,
            COALESCE(SUM({_bool_count_expr("o.reply_received")}), 0) AS reply_received,
            COALESCE(SUM({_bool_count_expr("o.agreement_reached")}), 0) AS agreement_reached,
            COALESCE(SUM({_bool_count_expr("o.content_published")}), 0) AS content_published,
            COALESCE(SUM({_bool_count_expr("o.order_attributed")}), 0) AS order_attributed,
            COALESCE(SUM(o.attributed_clicks), 0) AS attributed_clicks,
            COALESCE(SUM(o.attributed_orders), 0) AS attributed_orders,
            COALESCE(SUM(o.attributed_gmv_cents), 0) AS attributed_gmv_cents,
            COALESCE(SUM(o.attributed_cost_cents), 0) AS attributed_cost_cents,
            COALESCE(AVG(CASE WHEN o.computed_roi IS NOT NULL THEN o.computed_roi END), 0) AS avg_computed_roi
        FROM vkpi_kol_recommendations r
        LEFT JOIN vkpi_recommendation_outcomes o ON o.recommendation_id = r.id
        {clause}
        """,
        tuple(params),
    ).fetchone() or {})
    recommendations = int(totals.get("recommendations") or 0)
    conversion = {
        key: round(int(totals.get(key) or 0) / recommendations, 4) if recommendations else 0
        for key in (
            "shortlisted",
            "rejected",
            "claimed",
            "project_created",
            "outreach_sent",
            "reply_received",
            "agreement_reached",
            "content_published",
            "order_attributed",
        )
    }
    by_status = [dict(row) for row in conn.execute(
        f"""
        SELECT COALESCE(r.status, 'unknown') AS status, COUNT(*) AS count
        FROM vkpi_kol_recommendations r
        {clause}
        GROUP BY COALESCE(r.status, 'unknown')
        ORDER BY count DESC, status ASC
        """,
        tuple(params),
    ).fetchall()]
    by_platform = [dict(row) for row in conn.execute(
        f"""
        SELECT COALESCE(r.platform, 'other') AS platform,
               COUNT(*) AS recommendations,
               COALESCE(SUM({_bool_count_expr("o.project_created")}), 0) AS project_created,
               COALESCE(SUM({_bool_count_expr("o.order_attributed")}), 0) AS order_attributed,
               COALESCE(SUM(o.attributed_gmv_cents), 0) AS attributed_gmv_cents
        FROM vkpi_kol_recommendations r
        LEFT JOIN vkpi_recommendation_outcomes o ON o.recommendation_id = r.id
        {clause}
        GROUP BY COALESCE(r.platform, 'other')
        ORDER BY attributed_gmv_cents DESC, recommendations DESC
        """,
        tuple(params),
    ).fetchall()]
    source_rows = [dict(row) for row in conn.execute(
        f"""
        SELECT r.id AS recommendation_id, r.launch_id, r.run_id, r.kol_pool_id,
               r.linked_main_kol_id, r.platform, r.handle, r.display_name, r.rank, r.score,
               r.status, r.created_at AS recommended_at,
               o.id AS outcome_id, o.was_shortlisted, o.was_claimed, o.project_created,
               o.reply_received, o.agreement_reached, o.content_published, o.order_attributed,
               o.attributed_clicks, o.attributed_orders, o.attributed_gmv_cents,
               o.attributed_cost_cents, o.computed_roi, o.first_action_at, o.outcome_finalized_at,
               o.model_version
        FROM vkpi_kol_recommendations r
        LEFT JOIN vkpi_recommendation_outcomes o ON o.recommendation_id = r.id
        {clause}
        ORDER BY r.created_at DESC, r.rank ASC
        LIMIT ?
        """,
        (*params, max(1, min(200, int(limit or 50)))),
    ).fetchall()]
    return {
        "filters": {"launch_id": launch_id, "run_id": run_id},
        "totals": totals,
        "conversion": conversion,
        "by_status": by_status,
        "by_platform": by_platform,
        "source_rows": source_rows,
        "source_count": len(source_rows),
    }
