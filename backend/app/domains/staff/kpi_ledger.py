"""V-KPI employee workload and KPI ledger helpers.

The ledger is append-only at the source level: each employee action or business
source row gets a stable source_ref so rerunning the same day updates the value
instead of double-counting it.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime
from app.domains import audit
from app.domains.access import scope
from app.services.vkpi.kpi_evidence import enrich_kpi_source_row
from app.services.vkpi.schema import ensure_vkpi_schema
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema

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
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


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
        ORDER BY staff_id, project_id, metric_key, id
        """,
        (day, *params),
    )


def list_entries(limit: int = 100, staff_id: int | None = None, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    limit_i = max(1, min(500, int(limit or 100)))
    params: list[Any] = []
    where = ""
    scoped_staff_id = scope.effective_staff_id(staff, staff_id)
    if scoped_staff_id:
        where = "WHERE kl.staff_id=?"
        params.append(int(scoped_staff_id))
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


def generate_daily_rollup(ledger_date: str | None = None, staff_id: int | None = None, *, actor_staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    ensure_vkpi_product_industry_schema()
    day = _day(ledger_date)
    scoped_staff_id = scope.effective_staff_id(actor_staff, staff_id) if actor_staff else (int(staff_id) if staff_id else None)
    conn = get_conn()
    now = utcnow()
    status_counts: dict[str, int] = {"inserted": 0, "updated": 0}
    metric_counts: dict[str, int] = defaultdict(int)

    def upsert(**kwargs: Any) -> None:
        status = _upsert_entry(conn, ledger_date=day, now=now, **kwargs)
        _add_status(status_counts, status)
        metric_counts[str(kwargs.get("metric_key") or "")] += 1

    staff_filter, staff_params = _scope_clause(scoped_staff_id, "staff_id")

    # KOL claims: one source row per claim.
    for row in _fetchall(
        f"""
        SELECT id, staff_id, kol_id, project_id, status, claimed_at, created_at
        FROM vkpi_kol_claims
        WHERE {_day_where('COALESCE(claimed_at, created_at)')}{staff_filter}
          AND COALESCE(status, '') != 'released'
        """,
        (day, *staff_params),
    ):
        upsert(
            staff_id=_int(row.get("staff_id")) or None,
            kol_id=_int(row.get("kol_id")) or None,
            project_id=_int(row.get("project_id")) or None,
            metric_key="new_kol",
            metric_value=1,
            source_type="kol_claim",
            source_ref=_source_ref("claim", row.get("id")),
            metadata={"claim_id": row.get("id"), "status": row.get("status")},
        )

    project_filter, project_params = _scope_clause(scoped_staff_id, "assigned_staff_id")
    for row in _fetchall(
        f"""
        SELECT id, assigned_staff_id AS staff_id, kol_id, product_sku, project_name, created_at
        FROM vkpi_projects
        WHERE {_day_where('created_at')}{project_filter}
          AND COALESCE(stage_status, '') != 'deleted'
        """,
        (day, *project_params),
    ):
        upsert(
            staff_id=_int(row.get("staff_id")) or None,
            kol_id=_int(row.get("kol_id")) or None,
            project_id=_int(row.get("id")) or None,
            metric_key="project_created",
            metric_value=1,
            source_type="project",
            source_ref=_source_ref("project", row.get("id")),
            metadata={"project_name": row.get("project_name"), "product_sku": row.get("product_sku")},
        )

    event_filter, event_params = _scope_clause(scoped_staff_id, "e.actor_staff_id")
    for row in _fetchall(
        f"""
        SELECT e.id, e.project_id, e.to_stage, e.event_type, e.actor_staff_id AS staff_id,
               e.source_ref_type, e.source_ref_id, e.effective_at, p.kol_id, p.product_sku
        FROM vkpi_project_stage_events e
        LEFT JOIN vkpi_projects p ON p.id = e.project_id
        WHERE {_day_where('e.effective_at')}{event_filter}
        """,
        (day, *event_params),
    ):
        stage = str(row.get("to_stage") or "").strip().lower()
        if not stage:
            continue
        upsert(
            staff_id=_int(row.get("staff_id")) or None,
            kol_id=_int(row.get("kol_id")) or None,
            project_id=_int(row.get("project_id")) or None,
            metric_key=f"stage_{stage}",
            metric_value=1,
            source_type="project_stage_event",
            source_ref=_source_ref("stage", row.get("id")),
            metadata={"to_stage": stage, "event_type": row.get("event_type"), "product_sku": row.get("product_sku")},
        )

    link_filter, link_params = _scope_clause(scoped_staff_id, "staff_id")
    for row in _fetchall(
        f"""
        SELECT id, staff_id, kol_id, project_id, product_sku, slug, created_at
        FROM vkpi_links
        WHERE {_day_where('created_at')}{link_filter}
        """,
        (day, *link_params),
    ):
        upsert(
            staff_id=_int(row.get("staff_id")) or None,
            kol_id=_int(row.get("kol_id")) or None,
            project_id=_int(row.get("project_id")) or None,
            metric_key="link_created",
            metric_value=1,
            source_type="link",
            source_ref=_source_ref("link", row.get("id")),
            metadata={"slug": row.get("slug"), "product_sku": row.get("product_sku")},
        )

    click_filter, click_params = _scope_clause(scoped_staff_id, "l.staff_id")
    for row in _fetchall(
        f"""
        SELECT l.id AS link_id, l.staff_id, l.kol_id, l.project_id,
               COALESCE(SUM(CASE WHEN COALESCE(c.is_bot, 0)=0 THEN 1 ELSE 0 END), 0) AS valid_clicks,
               COALESCE(SUM(CASE WHEN COALESCE(c.is_bot, 0)=1 THEN 1 ELSE 0 END), 0) AS bot_clicks
        FROM vkpi_link_clicks c
        INNER JOIN vkpi_links l ON l.id = c.link_id
        WHERE {_day_where('c.clicked_at')}{click_filter}
        GROUP BY l.id, l.staff_id, l.kol_id, l.project_id
        """,
        (day, *click_params),
    ):
        valid = _int(row.get("valid_clicks"))
        bot = _int(row.get("bot_clicks"))
        if valid:
            upsert(
                staff_id=_int(row.get("staff_id")) or None,
                kol_id=_int(row.get("kol_id")) or None,
                project_id=_int(row.get("project_id")) or None,
                metric_key="valid_clicks",
                metric_value=valid,
                source_type="link_clicks",
                source_ref=f"daily-valid-clicks:{day}:link:{row.get('link_id')}",
                metadata={"link_id": row.get("link_id")},
            )
        if bot:
            upsert(
                staff_id=_int(row.get("staff_id")) or None,
                kol_id=_int(row.get("kol_id")) or None,
                project_id=_int(row.get("project_id")) or None,
                metric_key="bot_clicks",
                metric_value=bot,
                source_type="link_clicks",
                source_ref=f"daily-bot-clicks:{day}:link:{row.get('link_id')}",
                confidence="excluded",
                metadata={"link_id": row.get("link_id")},
            )

    content_filter, content_params = _scope_clause(scoped_staff_id, "p.assigned_staff_id")
    for row in _fetchall(
        f"""
        SELECT cp.id, cp.project_id, cp.kol_id, cp.link_id, cp.platform, cp.post_url,
               cp.views, cp.likes, cp.comments, cp.shares, cp.published_at, cp.created_at,
               p.assigned_staff_id AS staff_id, p.product_sku
        FROM vkpi_content_posts cp
        LEFT JOIN vkpi_projects p ON p.id = cp.project_id
        WHERE {_day_where('COALESCE(cp.published_at, cp.created_at)')}{content_filter}
        """,
        (day, *content_params),
    ):
        common = {
            "staff_id": _int(row.get("staff_id")) or None,
            "kol_id": _int(row.get("kol_id")) or None,
            "project_id": _int(row.get("project_id")) or None,
            "source_type": "content_post",
            "metadata": {"post_id": row.get("id"), "post_url": row.get("post_url"), "platform": row.get("platform"), "product_sku": row.get("product_sku")},
        }
        upsert(metric_key="published_content", metric_value=1, source_ref=_source_ref("content", row.get("id")), **common)
        if _int(row.get("views")):
            upsert(metric_key="content_views", metric_value=_int(row.get("views")), source_ref=f"content-views:{row.get('id')}", **common)
        if _int(row.get("likes")):
            upsert(metric_key="content_likes", metric_value=_int(row.get("likes")), source_ref=f"content-likes:{row.get('id')}", **common)

    cost_filter, cost_params = _scope_clause(scoped_staff_id, "staff_id")
    for row in _fetchall(
        f"""
        SELECT id, staff_id, kol_id, project_id, cost_type, amount_cents, status, source_ref, incurred_at
        FROM vkpi_cost_ledger
        WHERE {_day_where('incurred_at')}{cost_filter}
          AND COALESCE(status, '') != 'void'
        """,
        (day, *cost_params),
    ):
        upsert(
            staff_id=_int(row.get("staff_id")) or None,
            kol_id=_int(row.get("kol_id")) or None,
            project_id=_int(row.get("project_id")) or None,
            metric_key="cost_cents",
            metric_value=_int(row.get("amount_cents")),
            source_type="cost_ledger",
            source_ref=_source_ref("cost", row.get("id")),
            metadata={"cost_id": row.get("id"), "cost_type": row.get("cost_type"), "status": row.get("status"), "source_ref": row.get("source_ref")},
        )

    attribution_filter, attribution_params = _scope_clause(scoped_staff_id, "staff_id")
    for row in _fetchall(
        f"""
        SELECT id, staff_id, kol_id, project_id, link_id, source_platform, source_ref,
               revenue_cents, commission_cents, confidence, occurred_at, imported_at, created_at
        FROM vkpi_sales_attributions
        WHERE {_day_where('COALESCE(occurred_at, imported_at, created_at)')}{attribution_filter}
          AND COALESCE(confidence, '') NOT IN ('void', 'reversed', 'excluded')
        """,
        (day, *attribution_params),
    ):
        confidence = str(row.get("confidence") or "confirmed")
        metric_key = "estimated_revenue_cents" if confidence == "estimated" else "revenue_cents"
        upsert(
            staff_id=_int(row.get("staff_id")) or None,
            kol_id=_int(row.get("kol_id")) or None,
            project_id=_int(row.get("project_id")) or None,
            metric_key=metric_key,
            metric_value=_int(row.get("revenue_cents")),
            source_type="sales_attribution",
            source_ref=_source_ref("attribution", row.get("id")),
            confidence=confidence,
            metadata={"attribution_id": row.get("id"), "link_id": row.get("link_id"), "source_platform": row.get("source_platform"), "source_ref": row.get("source_ref")},
        )

    # Recommendation outcomes: tracks Product Analysis contribution without
    # double-counting actual sales/cost rows in financial metrics.
    recommendation_staff_filter, recommendation_staff_params = _scope_clause(scoped_staff_id, "rr.created_by_staff_id")
    recommendation_events = [
        ("recommendation_shortlisted", "o.was_shortlisted", "o.shortlisted_at", "shortlisted"),
        ("recommendation_rejected", "o.was_rejected", "o.rejected_at", "rejected"),
        ("recommendation_claimed", "o.was_claimed", "o.claimed_at", "claimed"),
        ("recommendation_project_created", "o.project_created", "o.project_created_at", "project_created"),
        ("recommendation_outreach_sent", "o.outreach_sent", "o.outreach_sent_at", "outreach_sent"),
        ("recommendation_reply_received", "o.reply_received", "o.reply_at", "reply_received"),
        ("recommendation_agreement_reached", "o.agreement_reached", "o.agreement_at", "agreement_reached"),
        ("recommendation_content_published", "o.content_published", "o.content_published_at", "content_published"),
        ("recommendation_order_attributed", "o.order_attributed", "o.first_order_at", "order_attributed"),
    ]
    for metric_key, bool_col, time_col, event_name in recommendation_events:
        for row in _fetchall(
            f"""
            SELECT o.id AS outcome_id, o.recommendation_id, r.launch_id, r.kol_pool_id,
                   r.linked_main_kol_id AS kol_id, r.platform, r.handle, r.score, r.rank,
                   rr.created_by_staff_id AS staff_id, {time_col} AS event_at
            FROM vkpi_recommendation_outcomes o
            INNER JOIN vkpi_kol_recommendations r ON r.id = o.recommendation_id
            LEFT JOIN vkpi_kol_recommendation_runs rr ON rr.id = r.run_id
            WHERE {_day_where(time_col)}
              AND {_bool_true_expr(bool_col)}
              AND rr.created_by_staff_id IS NOT NULL
              {recommendation_staff_filter}
            """,
            (day, *recommendation_staff_params),
        ):
            upsert(
                staff_id=_int(row.get("staff_id")) or None,
                kol_id=_int(row.get("kol_id")) or None,
                project_id=None,
                metric_key=metric_key,
                metric_value=1,
                source_type="recommendation_outcome",
                source_ref=f"recommendation-outcome:{event_name}:{row.get('recommendation_id')}",
                metadata={
                    "recommendation_id": row.get("recommendation_id"),
                    "outcome_id": row.get("outcome_id"),
                    "launch_id": row.get("launch_id"),
                    "kol_pool_id": row.get("kol_pool_id"),
                    "platform": row.get("platform"),
                    "handle": row.get("handle"),
                    "score": row.get("score"),
                    "rank": row.get("rank"),
                    "event": event_name,
                },
            )

    for row in _fetchall(
        f"""
        SELECT o.id AS outcome_id, o.recommendation_id, r.launch_id, r.kol_pool_id,
               r.linked_main_kol_id AS kol_id, r.platform, r.handle, r.score, r.rank,
               rr.created_by_staff_id AS staff_id, o.attributed_clicks, o.attributed_gmv_cents,
               o.attributed_cost_cents, o.computed_roi, o.first_order_at
        FROM vkpi_recommendation_outcomes o
        INNER JOIN vkpi_kol_recommendations r ON r.id = o.recommendation_id
        LEFT JOIN vkpi_kol_recommendation_runs rr ON rr.id = r.run_id
        WHERE {_day_where('o.first_order_at')}
          AND {_bool_true_expr('o.order_attributed')}
          AND rr.created_by_staff_id IS NOT NULL
          {recommendation_staff_filter}
        """,
        (day, *recommendation_staff_params),
    ):
        common = {
            "staff_id": _int(row.get("staff_id")) or None,
            "kol_id": _int(row.get("kol_id")) or None,
            "project_id": None,
            "source_type": "recommendation_outcome",
            "metadata": {
                "recommendation_id": row.get("recommendation_id"),
                "outcome_id": row.get("outcome_id"),
                "launch_id": row.get("launch_id"),
                "kol_pool_id": row.get("kol_pool_id"),
                "platform": row.get("platform"),
                "handle": row.get("handle"),
                "score": row.get("score"),
                "rank": row.get("rank"),
                "note": "recommendation metrics mirror outcome labels and do not drive financial double counting",
            },
        }
        if _int(row.get("attributed_clicks")):
            upsert(metric_key="recommendation_clicks", metric_value=_int(row.get("attributed_clicks")), source_ref=f"recommendation-outcome:clicks:{row.get('recommendation_id')}", **common)
        if _int(row.get("attributed_gmv_cents")):
            upsert(metric_key="recommendation_gmv_cents", metric_value=_int(row.get("attributed_gmv_cents")), source_ref=f"recommendation-outcome:gmv:{row.get('recommendation_id')}", **common)
        if _int(row.get("attributed_cost_cents")):
            upsert(metric_key="recommendation_cost_cents", metric_value=_int(row.get("attributed_cost_cents")), source_ref=f"recommendation-outcome:cost:{row.get('recommendation_id')}", **common)
        if row.get("computed_roi") is not None:
            upsert(metric_key="recommendation_roi", metric_value=_float(row.get("computed_roi")), source_ref=f"recommendation-outcome:roi:{row.get('recommendation_id')}", **common)

    # Derived project/staff financial metrics for the day.
    financials: dict[tuple[int, int], dict[str, float]] = defaultdict(lambda: {"revenue": 0, "cost": 0, "kol_id": 0})
    for row in _ledger_source_query(day, scoped_staff_id):
        sid = _int(row.get("staff_id"))
        pid = _int(row.get("project_id"))
        if not sid:
            continue
        key = (sid, pid)
        if row.get("kol_id"):
            financials[key]["kol_id"] = _int(row.get("kol_id"))
        metric = str(row.get("metric_key") or "")
        if metric in {"revenue_cents", "estimated_revenue_cents"}:
            financials[key]["revenue"] += _float(row.get("metric_value"))
        elif metric == "cost_cents":
            financials[key]["cost"] += _float(row.get("metric_value"))
    for (sid, pid), values in financials.items():
        revenue = values["revenue"]
        cost = values["cost"]
        if not revenue and not cost:
            continue
        base_ref = f"daily-financial:{day}:staff:{sid}:project:{pid or 0}"
        kol_id = _int(values.get("kol_id")) or None
        upsert(staff_id=sid, kol_id=kol_id, project_id=pid or None, metric_key="net_contribution_cents", metric_value=revenue - cost, source_type="derived_kpi", source_ref=f"{base_ref}:net", metadata={"revenue_cents": revenue, "cost_cents": cost})
        if cost:
            upsert(staff_id=sid, kol_id=kol_id, project_id=pid or None, metric_key="roi", metric_value=round(revenue / cost, 4), source_type="derived_kpi", source_ref=f"{base_ref}:roi", metadata={"revenue_cents": revenue, "cost_cents": cost})
            upsert(staff_id=sid, kol_id=kol_id, project_id=pid or None, metric_key="net_roi", metric_value=round((revenue - cost) / cost, 4), source_type="derived_kpi", source_ref=f"{base_ref}:net-roi", metadata={"revenue_cents": revenue, "cost_cents": cost})

    # Workload score and KPI credit per staff for the day.
    staff_scores: dict[int, dict[str, float]] = defaultdict(lambda: {"workload": 0, "net": 0})
    staff_components: dict[int, dict[str, dict[str, float]]] = defaultdict(dict)
    for row in _ledger_source_query(day, scoped_staff_id):
        sid = _int(row.get("staff_id"))
        if not sid:
            continue
        metric = str(row.get("metric_key") or "")
        value = _float(row.get("metric_value"))
        if metric in WORKLOAD_WEIGHTS:
            weight = float(WORKLOAD_WEIGHTS[metric])
            contribution = value * weight
            staff_scores[sid]["workload"] += contribution
            component = staff_components[sid].setdefault(
                metric,
                {"metric_value": 0.0, "weight": weight, "contribution": 0.0, "source_count": 0.0},
            )
            component["metric_value"] += value
            component["contribution"] += contribution
            component["source_count"] += 1
        elif metric == "net_contribution_cents":
            staff_scores[sid]["net"] += value
    for sid, values in staff_scores.items():
        workload = round(values["workload"], 4)
        net_credit = max(values["net"], 0) / 10000.0
        kpi_credit = round(workload + net_credit, 4)
        components = [
            {
                "metric_key": key,
                "metric_label": METRIC_LABELS.get(key, key),
                "metric_value": round(component["metric_value"], 4),
                "weight": component["weight"],
                "contribution": round(component["contribution"], 4),
                "source_count": int(component["source_count"]),
            }
            for key, component in sorted(staff_components.get(sid, {}).items())
        ]
        upsert(
            staff_id=sid,
            metric_key="workload_score",
            metric_value=workload,
            source_type="derived_kpi",
            source_ref=f"daily-workload:{day}:staff:{sid}",
            metadata={
                "formula": "sum(metric_value * workload_weight)",
                "components": components,
                "weights": WORKLOAD_WEIGHTS,
            },
        )
        upsert(
            staff_id=sid,
            metric_key="kpi_credit",
            metric_value=kpi_credit,
            source_type="derived_kpi",
            source_ref=f"daily-kpi-credit:{day}:staff:{sid}",
            metadata={
                "formula": "workload_score + max(net_contribution_cents, 0) / 10000",
                "workload_score": workload,
                "net_contribution_cents": round(values["net"], 4),
                "net_contribution_bonus": net_credit,
                "components": components,
            },
        )

    conn.commit()
    total_entries = conn.execute(
        "SELECT COUNT(*) AS n FROM vkpi_kpi_ledger WHERE ledger_date=?" + (" AND staff_id=?" if scoped_staff_id else ""),
        (day, scoped_staff_id) if scoped_staff_id else (day,),
    ).fetchone()["n"]
    try:
        audit.log_business_event(
            staff_id=scope.actor_staff_id(actor_staff) if actor_staff else (scoped_staff_id or 0),
            action_type="kpi_rollup",
            target_type="kpi_ledger",
            target_id=day,
            detail=f"generated KPI ledger for {day}",
            metadata={"ledger_date": day, "staff_id": scoped_staff_id, "metrics": dict(metric_counts), "status_counts": status_counts},
        )
    except Exception as exc:
        logger.warning("vkpi kpi rollup audit failed for %s: %s", day, exc)
    return {
        "ledger_date": day,
        "staff_id": scoped_staff_id,
        "inserted": int(status_counts.get("inserted", 0)),
        "updated": int(status_counts.get("updated", 0)),
        "total_entries": int(total_entries or 0),
        "metric_counts": dict(sorted(metric_counts.items())),
        "workload_weights": WORKLOAD_WEIGHTS,
    }
