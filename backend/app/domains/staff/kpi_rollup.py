"""Leaf runtime for the employee KPI daily rollup.

All application dependencies are injected by :mod:`kpi_ledger` so this module
can split the orchestration without importing the facade back and forming a
dependency cycle.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RollupDependencies:
    ensure_schema: Any
    ensure_product_schema: Any
    normalize_day: Any
    effective_staff_id: Any
    actor_staff_id: Any
    get_conn: Any
    utcnow: Any
    scope_clause: Any
    fetchall: Any
    day_where: Any
    bool_true_expr: Any
    upsert_entry: Any
    add_status: Any
    as_int: Any
    as_float: Any
    source_ref: Any
    ledger_source_query: Any
    current_kpi_ledger_sql: Any
    approved_actual_cost_sql: Any
    verified_attribution_sql: Any
    audit_log: Any
    log_warning: Any
    workload_weights: dict[str, float]
    metric_labels: dict[str, str]


@dataclass
class RollupContext:
    deps: RollupDependencies
    conn: Any
    day: str
    staff_id: int | None
    now: str
    status_counts: dict[str, int]
    metric_counts: dict[str, int]

    def upsert(self, **kwargs: Any) -> None:
        status = self.deps.upsert_entry(
            self.conn,
            ledger_date=self.day,
            now=self.now,
            **kwargs,
        )
        self.deps.add_status(self.status_counts, status)
        self.metric_counts[str(kwargs.get("metric_key") or "")] += 1


RECOMMENDATION_EVENTS = (
    ("recommendation_shortlisted", "o.was_shortlisted", "o.shortlisted_at", "shortlisted"),
    ("recommendation_rejected", "o.was_rejected", "o.rejected_at", "rejected"),
    ("recommendation_claimed", "o.was_claimed", "o.claimed_at", "claimed"),
    ("recommendation_project_created", "o.project_created", "o.project_created_at", "project_created"),
    ("recommendation_outreach_sent", "o.outreach_sent", "o.outreach_sent_at", "outreach_sent"),
    ("recommendation_reply_received", "o.reply_received", "o.reply_at", "reply_received"),
    ("recommendation_agreement_reached", "o.agreement_reached", "o.agreement_at", "agreement_reached"),
    ("recommendation_content_published", "o.content_published", "o.content_published_at", "content_published"),
    ("recommendation_order_attributed", "o.order_attributed", "o.first_order_at", "order_attributed"),
)


def _collect_claims(ctx: RollupContext) -> None:
    staff_filter, staff_params = ctx.deps.scope_clause(ctx.staff_id, "staff_id")
    for row in ctx.deps.fetchall(
        f"""
        SELECT id, staff_id, kol_id, project_id, status, claimed_at, created_at
        FROM vkpi_kol_claims
        WHERE {ctx.deps.day_where('COALESCE(claimed_at, created_at)')}{staff_filter}
          AND COALESCE(status, '') != 'released'
        """,
        (ctx.day, *staff_params),
    ):
        ctx.upsert(
            staff_id=ctx.deps.as_int(row.get("staff_id")) or None,
            kol_id=ctx.deps.as_int(row.get("kol_id")) or None,
            project_id=ctx.deps.as_int(row.get("project_id")) or None,
            metric_key="new_kol",
            metric_value=1,
            source_type="kol_claim",
            source_ref=ctx.deps.source_ref("claim", row.get("id")),
            metadata={"claim_id": row.get("id"), "status": row.get("status")},
        )


def _collect_projects(ctx: RollupContext) -> None:
    project_filter, project_params = ctx.deps.scope_clause(
        ctx.staff_id,
        "assigned_staff_id",
    )
    for row in ctx.deps.fetchall(
        f"""
        SELECT id, assigned_staff_id AS staff_id, kol_id, product_sku, project_name, created_at
        FROM vkpi_projects
        WHERE {ctx.deps.day_where('created_at')}{project_filter}
          AND COALESCE(stage_status, '') != 'deleted'
        """,
        (ctx.day, *project_params),
    ):
        ctx.upsert(
            staff_id=ctx.deps.as_int(row.get("staff_id")) or None,
            kol_id=ctx.deps.as_int(row.get("kol_id")) or None,
            project_id=ctx.deps.as_int(row.get("id")) or None,
            metric_key="project_created",
            metric_value=1,
            source_type="project",
            source_ref=ctx.deps.source_ref("project", row.get("id")),
            metadata={
                "project_name": row.get("project_name"),
                "product_sku": row.get("product_sku"),
            },
        )


def _collect_stage_events(ctx: RollupContext) -> None:
    event_filter, event_params = ctx.deps.scope_clause(
        ctx.staff_id,
        "e.actor_staff_id",
    )
    for row in ctx.deps.fetchall(
        f"""
        SELECT e.id, e.project_id, e.to_stage, e.event_type, e.actor_staff_id AS staff_id,
               e.source_ref_type, e.source_ref_id, e.effective_at, p.kol_id, p.product_sku
        FROM vkpi_project_stage_events e
        LEFT JOIN vkpi_projects p ON p.id = e.project_id
        WHERE {ctx.deps.day_where('e.effective_at')}{event_filter}
        """,
        (ctx.day, *event_params),
    ):
        stage = str(row.get("to_stage") or "").strip().lower()
        if not stage:
            continue
        ctx.upsert(
            staff_id=ctx.deps.as_int(row.get("staff_id")) or None,
            kol_id=ctx.deps.as_int(row.get("kol_id")) or None,
            project_id=ctx.deps.as_int(row.get("project_id")) or None,
            metric_key=f"stage_{stage}",
            metric_value=1,
            source_type="project_stage_event",
            source_ref=ctx.deps.source_ref("stage", row.get("id")),
            metadata={
                "to_stage": stage,
                "event_type": row.get("event_type"),
                "product_sku": row.get("product_sku"),
            },
        )


def _collect_links(ctx: RollupContext) -> None:
    link_filter, link_params = ctx.deps.scope_clause(ctx.staff_id, "staff_id")
    for row in ctx.deps.fetchall(
        f"""
        SELECT id, staff_id, kol_id, project_id, product_sku, slug, created_at
        FROM vkpi_links
        WHERE {ctx.deps.day_where('created_at')}{link_filter}
        """,
        (ctx.day, *link_params),
    ):
        ctx.upsert(
            staff_id=ctx.deps.as_int(row.get("staff_id")) or None,
            kol_id=ctx.deps.as_int(row.get("kol_id")) or None,
            project_id=ctx.deps.as_int(row.get("project_id")) or None,
            metric_key="link_created",
            metric_value=1,
            source_type="link",
            source_ref=ctx.deps.source_ref("link", row.get("id")),
            metadata={
                "slug": row.get("slug"),
                "product_sku": row.get("product_sku"),
            },
        )


def _collect_clicks(ctx: RollupContext) -> None:
    click_filter, click_params = ctx.deps.scope_clause(ctx.staff_id, "l.staff_id")
    for row in ctx.deps.fetchall(
        f"""
        SELECT l.id AS link_id, l.staff_id, l.kol_id, l.project_id,
               COALESCE(SUM(CASE WHEN COALESCE(c.is_bot, 0)=0 THEN 1 ELSE 0 END), 0) AS valid_clicks,
               COALESCE(SUM(CASE WHEN COALESCE(c.is_bot, 0)=1 THEN 1 ELSE 0 END), 0) AS bot_clicks
        FROM vkpi_link_clicks c
        INNER JOIN vkpi_links l ON l.id = c.link_id
        WHERE {ctx.deps.day_where('c.clicked_at')}{click_filter}
        GROUP BY l.id, l.staff_id, l.kol_id, l.project_id
        """,
        (ctx.day, *click_params),
    ):
        _upsert_click_metrics(ctx, row)


def _upsert_click_metrics(ctx: RollupContext, row: dict[str, Any]) -> None:
    valid = ctx.deps.as_int(row.get("valid_clicks"))
    bot = ctx.deps.as_int(row.get("bot_clicks"))
    common = {
        "staff_id": ctx.deps.as_int(row.get("staff_id")) or None,
        "kol_id": ctx.deps.as_int(row.get("kol_id")) or None,
        "project_id": ctx.deps.as_int(row.get("project_id")) or None,
        "source_type": "link_clicks",
        "metadata": {"link_id": row.get("link_id")},
    }
    if valid:
        ctx.upsert(
            metric_key="valid_clicks",
            metric_value=valid,
            source_ref=f"daily-valid-clicks:{ctx.day}:link:{row.get('link_id')}",
            **common,
        )
    if bot:
        ctx.upsert(
            metric_key="bot_clicks",
            metric_value=bot,
            source_ref=f"daily-bot-clicks:{ctx.day}:link:{row.get('link_id')}",
            confidence="excluded",
            **common,
        )


def _collect_content(ctx: RollupContext) -> None:
    content_filter, content_params = ctx.deps.scope_clause(
        ctx.staff_id,
        "p.assigned_staff_id",
    )
    for row in ctx.deps.fetchall(
        f"""
        SELECT cp.id, cp.project_id, cp.kol_id, cp.link_id, cp.platform, cp.post_url,
               cp.views, cp.likes, cp.comments, cp.shares, cp.published_at, cp.created_at,
               p.assigned_staff_id AS staff_id, p.product_sku
        FROM vkpi_content_posts cp
        LEFT JOIN vkpi_projects p ON p.id = cp.project_id
        WHERE {ctx.deps.day_where('COALESCE(cp.published_at, cp.created_at)')}{content_filter}
        """,
        (ctx.day, *content_params),
    ):
        _upsert_content_metrics(ctx, row)


def _upsert_content_metrics(ctx: RollupContext, row: dict[str, Any]) -> None:
    common = {
        "staff_id": ctx.deps.as_int(row.get("staff_id")) or None,
        "kol_id": ctx.deps.as_int(row.get("kol_id")) or None,
        "project_id": ctx.deps.as_int(row.get("project_id")) or None,
        "source_type": "content_post",
        "metadata": {
            "post_id": row.get("id"),
            "post_url": row.get("post_url"),
            "platform": row.get("platform"),
            "product_sku": row.get("product_sku"),
        },
    }
    ctx.upsert(
        metric_key="published_content",
        metric_value=1,
        source_ref=ctx.deps.source_ref("content", row.get("id")),
        **common,
    )
    if ctx.deps.as_int(row.get("views")):
        ctx.upsert(
            metric_key="content_views",
            metric_value=ctx.deps.as_int(row.get("views")),
            source_ref=f"content-views:{row.get('id')}",
            **common,
        )
    if ctx.deps.as_int(row.get("likes")):
        ctx.upsert(
            metric_key="content_likes",
            metric_value=ctx.deps.as_int(row.get("likes")),
            source_ref=f"content-likes:{row.get('id')}",
            **common,
        )


def _collect_costs(ctx: RollupContext) -> None:
    cost_filter, cost_params = ctx.deps.scope_clause(ctx.staff_id, "staff_id")
    for row in ctx.deps.fetchall(
        f"""
        SELECT id, staff_id, kol_id, project_id, cost_type, amount_cents, status, source_ref, incurred_at
        FROM vkpi_cost_ledger
        WHERE {ctx.deps.day_where('incurred_at')}{cost_filter}
          AND {ctx.deps.approved_actual_cost_sql()}
        """,
        (ctx.day, *cost_params),
    ):
        ctx.upsert(
            staff_id=ctx.deps.as_int(row.get("staff_id")) or None,
            kol_id=ctx.deps.as_int(row.get("kol_id")) or None,
            project_id=ctx.deps.as_int(row.get("project_id")) or None,
            metric_key="cost_cents",
            metric_value=ctx.deps.as_int(row.get("amount_cents")),
            source_type="cost_ledger",
            source_ref=ctx.deps.source_ref("cost", row.get("id")),
            metadata={
                "cost_id": row.get("id"),
                "cost_type": row.get("cost_type"),
                "status": row.get("status"),
                "source_ref": row.get("source_ref"),
            },
        )


def _collect_attributions(ctx: RollupContext) -> None:
    attribution_filter, attribution_params = ctx.deps.scope_clause(
        ctx.staff_id,
        "staff_id",
    )
    for row in ctx.deps.fetchall(
        f"""
        SELECT id, staff_id, kol_id, project_id, link_id, source_platform, source_ref,
               revenue_cents, commission_cents, confidence, occurred_at, imported_at, created_at
        FROM vkpi_sales_attributions
        WHERE {ctx.deps.day_where('COALESCE(occurred_at, imported_at, created_at)')}{attribution_filter}
          AND {ctx.deps.verified_attribution_sql()}
        """,
        (ctx.day, *attribution_params),
    ):
        confidence = str(row.get("confidence") or "confirmed")
        metric_key = (
            "estimated_revenue_cents"
            if confidence == "estimated"
            else "revenue_cents"
        )
        ctx.upsert(
            staff_id=ctx.deps.as_int(row.get("staff_id")) or None,
            kol_id=ctx.deps.as_int(row.get("kol_id")) or None,
            project_id=ctx.deps.as_int(row.get("project_id")) or None,
            metric_key=metric_key,
            metric_value=ctx.deps.as_int(row.get("revenue_cents")),
            source_type="sales_attribution",
            source_ref=ctx.deps.source_ref("attribution", row.get("id")),
            confidence=confidence,
            metadata={
                "attribution_id": row.get("id"),
                "link_id": row.get("link_id"),
                "source_platform": row.get("source_platform"),
                "source_ref": row.get("source_ref"),
            },
        )


def _recommendation_scope(ctx: RollupContext) -> tuple[str, tuple[Any, ...]]:
    return ctx.deps.scope_clause(ctx.staff_id, "rr.created_by_staff_id")


def _collect_recommendation_events(ctx: RollupContext) -> None:
    staff_filter, staff_params = _recommendation_scope(ctx)
    for metric_key, bool_col, time_col, event_name in RECOMMENDATION_EVENTS:
        rows = ctx.deps.fetchall(
            f"""
            SELECT o.id AS outcome_id, o.recommendation_id, r.launch_id, r.kol_pool_id,
                   r.linked_main_kol_id AS kol_id, r.platform, r.handle, r.score, r.rank,
                   rr.created_by_staff_id AS staff_id, {time_col} AS event_at
            FROM vkpi_recommendation_outcomes o
            INNER JOIN vkpi_kol_recommendations r ON r.id = o.recommendation_id
            LEFT JOIN vkpi_kol_recommendation_runs rr ON rr.id = r.run_id
            WHERE {ctx.deps.day_where(time_col)}
              AND {ctx.deps.bool_true_expr(bool_col)}
              AND rr.created_by_staff_id IS NOT NULL
              {staff_filter}
            """,
            (ctx.day, *staff_params),
        )
        for row in rows:
            _upsert_recommendation_event(
                ctx,
                row,
                metric_key=metric_key,
                event_name=event_name,
            )


def _upsert_recommendation_event(
    ctx: RollupContext,
    row: dict[str, Any],
    *,
    metric_key: str,
    event_name: str,
) -> None:
    ctx.upsert(
        staff_id=ctx.deps.as_int(row.get("staff_id")) or None,
        kol_id=ctx.deps.as_int(row.get("kol_id")) or None,
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


def _collect_recommendation_metrics(ctx: RollupContext) -> None:
    staff_filter, staff_params = _recommendation_scope(ctx)
    for row in ctx.deps.fetchall(
        f"""
        SELECT o.id AS outcome_id, o.recommendation_id, r.launch_id, r.kol_pool_id,
               r.linked_main_kol_id AS kol_id, r.platform, r.handle, r.score, r.rank,
               rr.created_by_staff_id AS staff_id, o.attributed_clicks, o.attributed_gmv_cents,
               o.attributed_cost_cents, o.computed_roi, o.first_order_at
        FROM vkpi_recommendation_outcomes o
        INNER JOIN vkpi_kol_recommendations r ON r.id = o.recommendation_id
        LEFT JOIN vkpi_kol_recommendation_runs rr ON rr.id = r.run_id
        WHERE {ctx.deps.day_where('o.first_order_at')}
          AND {ctx.deps.bool_true_expr('o.order_attributed')}
          AND rr.created_by_staff_id IS NOT NULL
          {staff_filter}
        """,
        (ctx.day, *staff_params),
    ):
        _upsert_recommendation_metrics(ctx, row)


def _recommendation_common(ctx: RollupContext, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "staff_id": ctx.deps.as_int(row.get("staff_id")) or None,
        "kol_id": ctx.deps.as_int(row.get("kol_id")) or None,
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


def _upsert_recommendation_metrics(
    ctx: RollupContext,
    row: dict[str, Any],
) -> None:
    common = _recommendation_common(ctx, row)
    recommendation_id = row.get("recommendation_id")
    if ctx.deps.as_int(row.get("attributed_clicks")):
        ctx.upsert(
            metric_key="recommendation_clicks",
            metric_value=ctx.deps.as_int(row.get("attributed_clicks")),
            source_ref=f"recommendation-outcome:clicks:{recommendation_id}",
            **common,
        )
    if ctx.deps.as_int(row.get("attributed_gmv_cents")):
        ctx.upsert(
            metric_key="recommendation_gmv_cents",
            metric_value=ctx.deps.as_int(row.get("attributed_gmv_cents")),
            source_ref=f"recommendation-outcome:gmv:{recommendation_id}",
            **common,
        )
    if ctx.deps.as_int(row.get("attributed_cost_cents")):
        ctx.upsert(
            metric_key="recommendation_cost_cents",
            metric_value=ctx.deps.as_int(row.get("attributed_cost_cents")),
            source_ref=f"recommendation-outcome:cost:{recommendation_id}",
            **common,
        )
    if row.get("computed_roi") is not None:
        ctx.upsert(
            metric_key="recommendation_roi",
            metric_value=ctx.deps.as_float(row.get("computed_roi")),
            source_ref=f"recommendation-outcome:roi:{recommendation_id}",
            **common,
        )


def _collect_financials(ctx: RollupContext) -> dict[tuple[int, int], dict[str, float]]:
    financials: dict[tuple[int, int], dict[str, float]] = defaultdict(
        lambda: {"revenue": 0, "cost": 0, "kol_id": 0}
    )
    for row in ctx.deps.ledger_source_query(ctx.day, ctx.staff_id):
        staff_id = ctx.deps.as_int(row.get("staff_id"))
        project_id = ctx.deps.as_int(row.get("project_id"))
        if not staff_id:
            continue
        values = financials[(staff_id, project_id)]
        if row.get("kol_id"):
            values["kol_id"] = ctx.deps.as_int(row.get("kol_id"))
        metric = str(row.get("metric_key") or "")
        if metric in {"revenue_cents", "estimated_revenue_cents"}:
            values["revenue"] += ctx.deps.as_float(row.get("metric_value"))
        elif metric == "cost_cents":
            values["cost"] += ctx.deps.as_float(row.get("metric_value"))
    return financials


def _derive_financials(ctx: RollupContext) -> None:
    for (staff_id, project_id), values in _collect_financials(ctx).items():
        revenue = values["revenue"]
        cost = values["cost"]
        if not revenue and not cost:
            continue
        base_ref = (
            f"daily-financial:{ctx.day}:staff:{staff_id}:"
            f"project:{project_id or 0}"
        )
        kol_id = ctx.deps.as_int(values.get("kol_id")) or None
        _upsert_financial_metrics(
            ctx,
            staff_id=staff_id,
            project_id=project_id,
            kol_id=kol_id,
            revenue=revenue,
            cost=cost,
            base_ref=base_ref,
        )


def _upsert_financial_metrics(
    ctx: RollupContext,
    *,
    staff_id: int,
    project_id: int,
    kol_id: int | None,
    revenue: float,
    cost: float,
    base_ref: str,
) -> None:
    common = {
        "staff_id": staff_id,
        "kol_id": kol_id,
        "project_id": project_id or None,
        "source_type": "derived_kpi",
        "metadata": {"revenue_cents": revenue, "cost_cents": cost},
    }
    ctx.upsert(
        metric_key="net_contribution_cents",
        metric_value=revenue - cost,
        source_ref=f"{base_ref}:net",
        **common,
    )
    if cost:
        ctx.upsert(
            metric_key="roi",
            metric_value=round(revenue / cost, 4),
            source_ref=f"{base_ref}:roi",
            **common,
        )
        ctx.upsert(
            metric_key="net_roi",
            metric_value=round((revenue - cost) / cost, 4),
            source_ref=f"{base_ref}:net-roi",
            **common,
        )


def _collect_staff_scores(
    ctx: RollupContext,
) -> tuple[dict[int, dict[str, float]], dict[int, dict[str, dict[str, float]]]]:
    scores: dict[int, dict[str, float]] = defaultdict(
        lambda: {"workload": 0, "net": 0}
    )
    components: dict[int, dict[str, dict[str, float]]] = defaultdict(dict)
    for row in ctx.deps.ledger_source_query(ctx.day, ctx.staff_id):
        staff_id = ctx.deps.as_int(row.get("staff_id"))
        if not staff_id:
            continue
        metric = str(row.get("metric_key") or "")
        value = ctx.deps.as_float(row.get("metric_value"))
        if metric in ctx.deps.workload_weights:
            _add_workload_component(
                ctx,
                scores,
                components,
                staff_id=staff_id,
                metric=metric,
                value=value,
            )
        elif metric == "net_contribution_cents":
            scores[staff_id]["net"] += value
    return scores, components


def _add_workload_component(
    ctx: RollupContext,
    scores: dict[int, dict[str, float]],
    components: dict[int, dict[str, dict[str, float]]],
    *,
    staff_id: int,
    metric: str,
    value: float,
) -> None:
    weight = float(ctx.deps.workload_weights[metric])
    contribution = value * weight
    scores[staff_id]["workload"] += contribution
    component = components[staff_id].setdefault(
        metric,
        {
            "metric_value": 0.0,
            "weight": weight,
            "contribution": 0.0,
            "source_count": 0.0,
        },
    )
    component["metric_value"] += value
    component["contribution"] += contribution
    component["source_count"] += 1


def _component_rows(
    ctx: RollupContext,
    components: dict[str, dict[str, float]],
) -> list[dict[str, Any]]:
    return [
        {
            "metric_key": key,
            "metric_label": ctx.deps.metric_labels.get(key, key),
            "metric_value": round(component["metric_value"], 4),
            "weight": component["weight"],
            "contribution": round(component["contribution"], 4),
            "source_count": int(component["source_count"]),
        }
        for key, component in sorted(components.items())
    ]


def _derive_staff_scores(ctx: RollupContext) -> None:
    scores, component_map = _collect_staff_scores(ctx)
    for staff_id, values in scores.items():
        workload = round(values["workload"], 4)
        net_credit = max(values["net"], 0) / 10000.0
        kpi_credit = round(workload + net_credit, 4)
        components = _component_rows(ctx, component_map.get(staff_id, {}))
        ctx.upsert(
            staff_id=staff_id,
            metric_key="workload_score",
            metric_value=workload,
            source_type="derived_kpi",
            source_ref=f"daily-workload:{ctx.day}:staff:{staff_id}",
            metadata={
                "formula": "sum(metric_value * workload_weight)",
                "components": components,
                "weights": ctx.deps.workload_weights,
            },
        )
        ctx.upsert(
            staff_id=staff_id,
            metric_key="kpi_credit",
            metric_value=kpi_credit,
            source_type="derived_kpi",
            source_ref=f"daily-kpi-credit:{ctx.day}:staff:{staff_id}",
            metadata={
                "formula": "workload_score + max(net_contribution_cents, 0) / 10000",
                "workload_score": workload,
                "net_contribution_cents": round(values["net"], 4),
                "net_contribution_bonus": net_credit,
                "components": components,
            },
        )


def _total_entries(ctx: RollupContext) -> Any:
    total_where = (
        "WHERE ledger_date=? AND " + ctx.deps.current_kpi_ledger_sql()
    )
    if ctx.staff_id:
        total_where += " AND staff_id=?"
    row = ctx.conn.execute(
        "SELECT COUNT(*) AS n FROM vkpi_kpi_ledger " + total_where,
        (ctx.day, ctx.staff_id) if ctx.staff_id else (ctx.day,),
    ).fetchone()
    return row["n"]


def _log_audit(
    ctx: RollupContext,
    actor_staff: dict[str, Any] | None,
) -> None:
    try:
        ctx.deps.audit_log(
            staff_id=(
                ctx.deps.actor_staff_id(actor_staff)
                if actor_staff
                else (ctx.staff_id or 0)
            ),
            action_type="kpi_rollup",
            target_type="kpi_ledger",
            target_id=ctx.day,
            detail=f"generated KPI ledger for {ctx.day}",
            metadata={
                "ledger_date": ctx.day,
                "staff_id": ctx.staff_id,
                "metrics": dict(ctx.metric_counts),
                "status_counts": ctx.status_counts,
            },
        )
    except Exception as exc:
        ctx.deps.log_warning(
            "vkpi kpi rollup audit failed for %s: %s",
            ctx.day,
            exc,
        )


def _result(ctx: RollupContext, total_entries: Any) -> dict[str, Any]:
    return {
        "ledger_date": ctx.day,
        "staff_id": ctx.staff_id,
        "inserted": int(ctx.status_counts.get("inserted", 0)),
        "updated": int(ctx.status_counts.get("updated", 0)),
        "total_entries": int(total_entries or 0),
        "metric_counts": dict(sorted(ctx.metric_counts.items())),
        "workload_weights": ctx.deps.workload_weights,
    }


def generate_daily_rollup(
    ledger_date: str | None,
    staff_id: int | None,
    *,
    actor_staff: dict[str, Any] | None,
    deps: RollupDependencies,
) -> dict[str, Any]:
    deps.ensure_schema()
    deps.ensure_product_schema()
    day = deps.normalize_day(ledger_date)
    scoped_staff_id = (
        deps.effective_staff_id(actor_staff, staff_id)
        if actor_staff
        else (int(staff_id) if staff_id else None)
    )
    ctx = RollupContext(
        deps=deps,
        conn=deps.get_conn(),
        day=day,
        staff_id=scoped_staff_id,
        now=deps.utcnow(),
        status_counts={"inserted": 0, "updated": 0},
        metric_counts=defaultdict(int),
    )
    _collect_claims(ctx)
    _collect_projects(ctx)
    _collect_stage_events(ctx)
    _collect_links(ctx)
    _collect_clicks(ctx)
    _collect_content(ctx)
    _collect_costs(ctx)
    _collect_attributions(ctx)
    _collect_recommendation_events(ctx)
    _collect_recommendation_metrics(ctx)
    _derive_financials(ctx)
    _derive_staff_scores(ctx)
    ctx.conn.commit()
    total_entries = _total_entries(ctx)
    _log_audit(ctx, actor_staff)
    return _result(ctx, total_entries)
