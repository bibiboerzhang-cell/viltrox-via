"""V-KPI employee workload and KPI ledger helpers.

The ledger is append-only at the source level: each employee action or business
source row gets a stable source_ref so rerunning the same day updates the value
instead of double-counting it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime
from app.domains import audit, business_truth
from app.domains.access import scope
from app.domains.staff import kpi_rollup as _rollup_runtime
from app.shared.vkpi_kpi_evidence import enrich_kpi_source_row
from app.platform.db.schema import ensure_vkpi_schema
from app.platform.db.schema_product_industry import ensure_vkpi_product_industry_schema

logger = get_logger(__name__)


STAGE_WEIGHTS: dict[str, float] = {
    "contacted": 1,
    "replied": 2,
    "agreed": 4,
    "shipped": 3,
    "received": 1,
    "published": 5,
    "content_published": 5,
    "measured": 3,
    "closed": 1,
}

WORKLOAD_WEIGHTS: dict[str, float] = {
    "new_kol": 2,
    "project_created": 1,
    "link_created": 1,
    "published_content": 5,
    "valid_clicks": 0.02,
    "recommendation_shortlisted": 0.5,
    "recommendation_claimed": 1,
    "recommendation_project_created": 2,
    "recommendation_reply_received": 2,
    "recommendation_agreement_reached": 4,
    "recommendation_content_published": 5,
    "recommendation_order_attributed": 5,
    **{f"stage_{stage}": weight for stage, weight in STAGE_WEIGHTS.items()},
}

METRIC_LABELS: dict[str, str] = {
    "new_kol": "新增 KOL",
    "project_created": "创建项目",
    "stage_contacted": "已联系",
    "stage_replied": "已回复",
    "stage_agreed": "已合作",
    "stage_shipped": "已发货",
    "stage_received": "已到货",
    "stage_published": "已发布",
    "stage_content_published": "已发布",
    "stage_measured": "已统计",
    "stage_closed": "已关闭",
    "link_created": "创建短链",
    "valid_clicks": "有效点击",
    "bot_clicks": "机器人点击",
    "published_content": "发布内容",
    "recommendation_shortlisted": "推荐入选",
    "recommendation_rejected": "推荐拒绝",
    "recommendation_claimed": "推荐认领",
    "recommendation_project_created": "推荐建项",
    "recommendation_outreach_sent": "推荐触达",
    "recommendation_reply_received": "推荐回复",
    "recommendation_agreement_reached": "推荐合作",
    "recommendation_content_published": "推荐发布",
    "recommendation_order_attributed": "推荐出单",
    "recommendation_clicks": "推荐点击",
    "recommendation_gmv_cents": "推荐销售额",
    "recommendation_cost_cents": "推荐成本",
    "recommendation_roi": "推荐 ROI",
    "content_views": "内容播放量",
    "content_likes": "内容点赞",
    "revenue_cents": "销售额",
    "estimated_revenue_cents": "估算销售额",
    "cost_cents": "成本",
    "net_contribution_cents": "净贡献",
    "roi": "ROI",
    "net_roi": "Net ROI",
    "workload_score": "工作量分",
    "kpi_credit": "KPI Credit",
}


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _parse_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except Exception as exc:
        logger.warning("vkpi kpi ledger json parse failed: %s", exc)
        return {}


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def _day(value: str | None) -> str:
    raw = str(value or utcnow()[:10]).strip()
    return raw[:10]


def _row(item: Any) -> dict[str, Any]:
    return dict(item) if item is not None else {}


def _fetchall(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [_row(row) for row in get_conn().execute(sql, params).fetchall()]


def _day_where(column_sql: str) -> str:
    if is_postgres_runtime():
        return f"to_char((COALESCE({column_sql}, NOW()) AT TIME ZONE 'UTC'), 'YYYY-MM-DD')=?"
    return f"substr(CAST(COALESCE(NULLIF({column_sql}, ''), '') AS TEXT), 1, 10)=?"


def _bool_true_expr(column_sql: str) -> str:
    if is_postgres_runtime():
        return f"COALESCE({column_sql}, FALSE) = TRUE"
    return f"COALESCE({column_sql}, 0)=1"


def _source_ref(prefix: str, source_id: Any) -> str:
    return f"{prefix}:{source_id}"


def _upsert_entry(
    conn,
    *,
    ledger_date: str,
    staff_id: int | None,
    kol_id: int | None = None,
    project_id: int | None = None,
    metric_key: str,
    metric_value: float,
    source_type: str,
    source_ref: str,
    confidence: str = "confirmed",
    metadata: dict[str, Any] | None = None,
    now: str | None = None,
) -> str:
    now = now or utcnow()
    existing = conn.execute(
        "SELECT id FROM vkpi_kpi_ledger WHERE ledger_date=? AND metric_key=? AND source_ref=?",
        (ledger_date, metric_key, source_ref),
    ).fetchone()
    payload = _json(metadata or {})
    if existing:
        conn.execute(
            """
            UPDATE vkpi_kpi_ledger
            SET staff_id=?, kol_id=?, project_id=?, metric_value=?, source_type=?, confidence=?, metadata_json=?
            WHERE id=?
            """,
            (staff_id, kol_id, project_id, float(metric_value), source_type, confidence, payload, int(existing["id"])),
        )
        return "updated"
    conn.execute(
        """
        INSERT INTO vkpi_kpi_ledger (
            ledger_date, staff_id, kol_id, project_id, metric_key, metric_value,
            source_type, source_ref, confidence, metadata_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            ledger_date,
            staff_id,
            kol_id,
            project_id,
            metric_key,
            float(metric_value),
            source_type,
            source_ref,
            confidence,
            payload,
            now,
        ),
    )
    return "inserted"


def _add_status(counters: dict[str, int], status: str) -> None:
    counters[status] = int(counters.get(status, 0)) + 1


def _scope_clause(staff_id: int | None, column: str) -> tuple[str, tuple[Any, ...]]:
    if not staff_id:
        return "", ()
    return f" AND {column}=?", (int(staff_id),)


def _ledger_source_query(day: str, staff_id: int | None = None) -> list[dict[str, Any]]:
    staff_clause, params = _scope_clause(staff_id, "staff_id")
    return _fetchall(
        f"""
        SELECT *
        FROM vkpi_kpi_ledger
        WHERE ledger_date=?{staff_clause}
          AND {business_truth.current_kpi_ledger_sql()}
        ORDER BY staff_id, project_id, metric_key, id
        """,
        (day, *params),
    )


def list_entries(limit: int = 100, staff_id: int | None = None, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    limit_i = max(1, min(500, int(limit or 100)))
    params: list[Any] = []
    where_parts: list[str] = [business_truth.current_kpi_ledger_sql("kl")]
    scoped_staff_id = scope.effective_staff_id(staff, staff_id)
    if scoped_staff_id:
        where_parts.append("kl.staff_id=?")
        params.append(int(scoped_staff_id))
    where = "WHERE " + " AND ".join(where_parts)
    rows = get_conn().execute(
        f"""
        SELECT kl.*,
               COALESCE(u.name, u.email, '') AS staff_name,
               COALESCE(u.creator_code, '') AS employee_code,
               COALESCE(k.channel_name, '') AS kol_name,
               COALESCE(k.platform, '') AS platform,
               COALESCE(p.project_name, '') AS project_name,
               COALESCE(p.product_sku, '') AS product_sku
        FROM vkpi_kpi_ledger kl
        LEFT JOIN staff s ON s.id = kl.staff_id
        LEFT JOIN users u ON u.id = s.user_id
        LEFT JOIN kols k ON k.id = kl.kol_id
        LEFT JOIN vkpi_projects p ON p.id = kl.project_id
        {where}
        ORDER BY kl.ledger_date DESC, kl.id DESC
        LIMIT ?
        """,
        (*params, limit_i),
    ).fetchall()
    entries: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["metric_label"] = METRIC_LABELS.get(str(item.get("metric_key") or ""), str(item.get("metric_key") or ""))
        item["metadata"] = _parse_json(item.get("metadata_json"))
        item = enrich_kpi_source_row(get_conn(), item)
        item["metric_label"] = METRIC_LABELS.get(str(item.get("metric_key") or ""), str(item.get("metric_key") or ""))
        entries.append(item)
    return {"entries": entries, "metric_labels": METRIC_LABELS, "workload_weights": WORKLOAD_WEIGHTS}


def _rollup_dependencies() -> _rollup_runtime.RollupDependencies:
    return _rollup_runtime.RollupDependencies(
        ensure_schema=ensure_vkpi_schema,
        ensure_product_schema=ensure_vkpi_product_industry_schema,
        normalize_day=_day,
        effective_staff_id=scope.effective_staff_id,
        actor_staff_id=scope.actor_staff_id,
        get_conn=get_conn,
        utcnow=utcnow,
        scope_clause=_scope_clause,
        fetchall=_fetchall,
        day_where=_day_where,
        bool_true_expr=_bool_true_expr,
        upsert_entry=_upsert_entry,
        add_status=_add_status,
        as_int=_int,
        as_float=_float,
        source_ref=_source_ref,
        ledger_source_query=_ledger_source_query,
        current_kpi_ledger_sql=business_truth.current_kpi_ledger_sql,
        approved_actual_cost_sql=business_truth.approved_actual_cost_sql,
        verified_attribution_sql=business_truth.verified_shopify_attribution_sql,
        audit_log=audit.log_business_event,
        log_warning=logger.warning,
        workload_weights=WORKLOAD_WEIGHTS,
        metric_labels=METRIC_LABELS,
    )


def generate_daily_rollup(
    ledger_date: str | None = None,
    staff_id: int | None = None,
    *,
    actor_staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _rollup_runtime.generate_daily_rollup(
        ledger_date,
        staff_id,
        actor_staff=actor_staff,
        deps=_rollup_dependencies(),
    )
