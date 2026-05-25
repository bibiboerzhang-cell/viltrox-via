"""Training dataset export for future V-KPI ML scoring."""
from __future__ import annotations

import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from app.db.connection import get_conn
from app.domains.recommendations import outcomes as outcome_collector
from app.platform.db.schema_product_industry import ensure_vkpi_product_industry_schema
from app.domains.projects.workflow import staff_id as resolve_staff_id

PROJECT_ROOT = Path(__file__).resolve().parents[4]
EXPORT_DIR = PROJECT_ROOT / "runtime" / "vkpi_training_exports"


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _loads(value: Any, default: Any = None) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return default


def export_training_dataset(date_from: str = "", date_to: str = "", *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    uid = f"train-{secrets.token_hex(8)}"
    now = _utcnow()
    path = EXPORT_DIR / f"{uid}.jsonl"
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_training_exports
            (export_uid, date_from, date_to, file_path, row_count, status, created_by_staff_id, created_at, metadata_json)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (uid, date_from or "", date_to or "", str(path), 0, "running", resolve_staff_id(staff) or None, now, "{}"),
    )
    where = []
    params: list[Any] = []
    if date_from:
        where.append("r.created_at>=?")
        params.append(date_from)
    if date_to:
        where.append("r.created_at<=?")
        params.append(date_to)
    clause = "WHERE " + " AND ".join(where) if where else ""
    recommendation_ids = conn.execute(
        f"SELECT r.id FROM vkpi_kol_recommendations r {clause} ORDER BY r.created_at ASC, r.id ASC",
        tuple(params),
    ).fetchall()
    for rec in recommendation_ids:
        outcome_collector.refresh_business_outcome(int(rec["id"]))
    rows = conn.execute(
        f"""
        SELECT
            r.id AS recommendation_id,
            r.launch_id AS launch_id,
            r.kol_pool_id AS kol_pool_id,
            r.platform AS recommendation_platform,
            r.handle AS recommendation_handle,
            r.score AS score,
            r.rank AS rank,
            r.status AS recommendation_status,
            r.feature_snapshot_json AS recommendation_feature_snapshot_json,
            r.scoring_breakdown_json AS recommendation_scoring_breakdown_json,
            o.id AS outcome_id,
            o.was_shortlisted,
            o.shortlisted_at,
            o.was_rejected,
            o.rejected_at,
            o.reject_reason,
            o.was_claimed,
            o.claimed_at,
            o.project_created,
            o.project_created_at,
            o.outreach_sent,
            o.outreach_sent_at,
            o.reply_received,
            o.reply_at,
            o.reply_sentiment,
            o.agreement_reached,
            o.agreement_at,
            o.content_published,
            o.content_published_at,
            o.content_url,
            o.order_attributed,
            o.first_order_at,
            o.attributed_clicks,
            o.attributed_orders,
            o.attributed_gmv_cents,
            o.attributed_cost_cents,
            o.computed_roi,
            o.recommended_at,
            o.first_action_at,
            o.outcome_finalized_at,
            o.feature_snapshot_json AS outcome_feature_snapshot_json,
            o.scoring_breakdown_json AS outcome_scoring_breakdown_json,
            o.model_version,
            o.display_position,
            o.display_context_json,
            p.handle AS pool_handle,
            p.platform AS pool_platform,
            l.product_sku,
            l.product_name
        FROM vkpi_kol_recommendations r
        LEFT JOIN vkpi_recommendation_outcomes o ON o.recommendation_id = r.id
        LEFT JOIN vkpi_kol_pool p ON p.id = r.kol_pool_id
        LEFT JOIN vkpi_product_launches l ON l.id = r.launch_id
        {clause}
        ORDER BY r.created_at ASC, r.id ASC
        """,
        tuple(params),
    ).fetchall()
    count = 0
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            item = dict(row)
            record = {
                "recommendation_id": item.get("recommendation_id"),
                "launch_id": item.get("launch_id"),
                "kol_pool_id": item.get("kol_pool_id"),
                "platform": item.get("pool_platform") or item.get("recommendation_platform"),
                "handle": item.get("pool_handle") or item.get("recommendation_handle"),
                "product_sku": item.get("product_sku"),
                "product_name": item.get("product_name"),
                "score": item.get("score"),
                "rank": item.get("rank"),
                "status": item.get("recommendation_status"),
                "feature_snapshot": _loads(item.get("outcome_feature_snapshot_json") or item.get("recommendation_feature_snapshot_json"), {}),
                "scoring_breakdown": _loads(item.get("outcome_scoring_breakdown_json") or item.get("recommendation_scoring_breakdown_json"), {}),
                "outcome": {key: value for key, value in item.items() if key.startswith("was_") or key in {"project_created", "outreach_sent", "reply_received", "agreement_reached", "content_published", "attributed_clicks", "attributed_orders", "attributed_gmv_cents", "attributed_cost_cents", "computed_roi"}},
            }
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            count += 1
    conn.execute("UPDATE vkpi_training_exports SET row_count=?, status='completed', completed_at=? WHERE export_uid=?", (count, _utcnow(), uid))
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_training_exports WHERE export_uid=?", (uid,)).fetchone()
    return {"export": dict(row) if row else {"export_uid": uid, "file_path": str(path), "row_count": count}}


def latest(limit: int = 20) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    rows = get_conn().execute(
        "SELECT * FROM vkpi_training_exports ORDER BY created_at DESC, id DESC LIMIT ?",
        (max(1, min(100, int(limit or 20))),),
    ).fetchall()
    return {"exports": [dict(row) for row in rows]}
