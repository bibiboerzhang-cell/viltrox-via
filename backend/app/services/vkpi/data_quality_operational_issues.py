"""Operational data quality checks appended to the main issue list."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.services.vkpi import scope
from app.services.vkpi.data_quality_common import _append_issue, _safe_rows, _staff_clause


def append_operational_quality_issues(
    *,
    conn: Any,
    issues: list[dict[str, Any]],
    staff: dict[str, Any] | None,
    max_items: int,
) -> None:
    # Shopify order snapshots exist but no Shopify sync/run has refreshed recently.
    if scope.can_view_all(staff):
        latest_snapshot = conn.execute(
            "SELECT id, shopify_order_id, updated_at FROM vkpi_shopify_order_snapshots ORDER BY updated_at DESC, id DESC LIMIT 1"
        ).fetchone()
        if latest_snapshot:
            snapshot = dict(latest_snapshot)
            stale_before = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
            if str(snapshot.get("updated_at") or "") < stale_before:
                _append_issue(
                    issues,
                    issue_type="stale_shopify_snapshot",
                    severity="low",
                    title="Shopify 订单快照超过 24 小时未刷新",
                    entity_type="shopify_order_snapshot",
                    entity_id=snapshot.get("id"),
                    detail=str(snapshot.get("shopify_order_id") or ""),
                    evidence=snapshot,
                )

    # Stale dashboard metric snapshot.
    stale_before = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = conn.execute(
        """
        SELECT id, run_uid, generated_at, scope_type, scope_id
        FROM vkpi_metric_runs
        WHERE trigger_source='dashboard' AND status='ready'
        ORDER BY generated_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        _append_issue(
            issues,
            issue_type="missing_metric_snapshot",
            severity="medium",
            title="还没有 Dashboard 指标快照",
            entity_type="metric_run",
            detail="打开管理主控或手动运行 lineage 后会生成。",
        )
    elif str(dict(row).get("generated_at") or "") < stale_before:
        item = dict(row)
        _append_issue(
            issues,
            issue_type="stale_metric_snapshot",
            severity="medium",
            title="Dashboard 指标快照超过 24 小时未刷新",
            entity_type="metric_run",
            entity_id=item.get("id"),
            detail=str(item.get("run_uid") or ""),
            evidence=item,
        )

    # Non-zero dashboard metric values must have source rows.
    metric_scope_sql = ""
    metric_scope_params: list[Any] = []
    if not scope.can_view_all(staff):
        actor = scope.actor_staff_id(staff)
        if actor:
            metric_scope_sql = " AND (mr.scope_type='staff' AND mr.scope_id=?) "
            metric_scope_params = [actor]
        else:
            metric_scope_sql = " AND 1=0 "
    rows = _safe_rows(
        conn,
        f"""
        SELECT mv.id AS metric_value_id,
               mv.metric_key,
               mv.value_numeric,
               mv.unit,
               mv.currency,
               mv.source_count,
               mv.created_at,
               mr.id AS run_id,
               mr.run_uid,
               mr.scope_type,
               mr.scope_id,
               mr.generated_at,
               COALESCE(src.actual_source_count, 0) AS actual_source_count
        FROM vkpi_metric_values mv
        INNER JOIN vkpi_metric_runs mr ON mr.id = mv.run_id
        LEFT JOIN (
            SELECT metric_value_id, COUNT(*) AS actual_source_count
            FROM vkpi_metric_sources
            GROUP BY metric_value_id
        ) src ON src.metric_value_id = mv.id
        WHERE mr.status='ready'
          AND COALESCE(mv.value_numeric, 0) != 0
          AND COALESCE(mv.source_count, 0) = 0
          AND COALESCE(src.actual_source_count, 0) = 0
        {metric_scope_sql}
        ORDER BY mv.created_at DESC, mv.id DESC
        LIMIT ?
        """,
        (*metric_scope_params, max_items),
    )
    for item in rows:
        _append_issue(
            issues,
            issue_type="metric_without_sources",
            severity="high",
            title="Dashboard 指标有数值但没有来源行",
            entity_type="metric_value",
            entity_id=item.get("metric_value_id"),
            detail=f"{item.get('metric_key')} · {item.get('run_uid')}",
            evidence=item,
        )

    # KPI ledger rows must preserve enough evidence to explain staff credit.
    kpi_staff_sql, kpi_staff_params = _staff_clause("kl.staff_id", staff)
    rows = _safe_rows(
        conn,
        f"""
        SELECT kl.id,
               kl.ledger_date,
               kl.staff_id,
               kl.kol_id,
               kl.project_id,
               kl.metric_key,
               kl.metric_value,
               kl.source_type,
               kl.source_ref,
               kl.confidence,
               kl.metadata_json,
               kl.created_at
        FROM vkpi_kpi_ledger kl
        WHERE (
            TRIM(COALESCE(kl.source_type,'')) = ''
            OR TRIM(COALESCE(kl.source_ref,'')) = ''
            OR (
                kl.metric_key IN ('workload_score','kpi_credit','roi','net_roi','net_contribution_cents')
                AND (
                    TRIM(COALESCE(kl.metadata_json,'')) IN ('','{{}}','null')
                    OR COALESCE(kl.metadata_json,'') NOT LIKE ?
                )
            )
        )
        {kpi_staff_sql}
        ORDER BY kl.created_at DESC, kl.id DESC
        LIMIT ?
        """,
        ("%formula%", *kpi_staff_params, max_items),
    )
    for item in rows:
        _append_issue(
            issues,
            issue_type="kpi_ledger_without_evidence",
            severity="medium",
            title="员工 KPI 记录缺少来源或公式证据",
            entity_type="kpi_ledger",
            entity_id=item.get("id"),
            staff_id=item.get("staff_id"),
            project_id=item.get("project_id"),
            kol_id=item.get("kol_id"),
            detail=f"{item.get('metric_key')} · {item.get('source_ref') or 'missing source'}",
            evidence=item,
        )

    # Ready weekly reports must be tied to metric sources for appendix/downstream PDF evidence.
    report_scope_sql = ""
    report_scope_params: list[Any] = []
    if not scope.can_view_all(staff):
        actor = scope.actor_staff_id(staff)
        if actor:
            report_scope_sql = " AND r.scope_type='staff' AND r.scope_id=? "
            report_scope_params = [actor]
        else:
            report_scope_sql = " AND 1=0 "
    rows = _safe_rows(
        conn,
        f"""
        SELECT r.id,
               r.report_uid,
               r.report_type,
               r.period_start,
               r.period_end,
               r.scope_type,
               r.scope_id,
               r.metric_run_id,
               r.triggered_by_staff_id,
               r.triggered_at,
               r.status,
               r.summary_text,
               COALESCE(src.source_rows, 0) AS source_rows
        FROM vkpi_report_runs r
        LEFT JOIN (
            SELECT mv.run_id, COUNT(ms.id) AS source_rows
            FROM vkpi_metric_values mv
            LEFT JOIN vkpi_metric_sources ms ON ms.metric_value_id = mv.id
            GROUP BY mv.run_id
        ) src ON src.run_id = r.metric_run_id
        WHERE r.report_type='weekly'
          AND r.status='ready'
          AND (
            r.metric_run_id IS NULL
            OR COALESCE(src.source_rows, 0) = 0
          )
        {report_scope_sql}
        ORDER BY r.triggered_at DESC, r.id DESC
        LIMIT ?
        """,
        (*report_scope_params, max_items),
    )
    for item in rows:
        _append_issue(
            issues,
            issue_type="weekly_report_without_source_appendix",
            severity="medium",
            title="周报缺少指标来源附录",
            entity_type="report_run",
            entity_id=item.get("id"),
            staff_id=item.get("triggered_by_staff_id"),
            detail=str(item.get("report_uid") or ""),
            evidence=item,
        )

    # KOLs already in active workflow or attribution need real contact data.
    kol_contact_where = ""
    kol_contact_params: list[Any] = []
    if not scope.can_view_all(staff):
        actor = scope.actor_staff_id(staff)
        if actor:
            kol_contact_where = " AND (k.assigned_staff_id=? OR k.created_by_staff_id=? OR p.assigned_staff_id=? OR p.created_by_staff_id=? OR c.staff_id=?) "
            kol_contact_params = [actor, actor, actor, actor, actor]
        else:
            kol_contact_where = " AND 1=0 "
    rows = _safe_rows(
        conn,
        f"""
        SELECT k.id,
               k.platform,
               k.channel_name,
               k.channel_url,
               k.contact_email,
               k.contact_phone,
               k.contact_links_json,
               k.assigned_staff_id,
               p.id AS project_id,
               p.project_name,
               p.assigned_staff_id AS project_staff_id,
               c.staff_id AS claim_staff_id,
               COUNT(DISTINCT sa.id) AS attribution_count,
               COALESCE(SUM(sa.revenue_cents), 0) AS revenue_cents
        FROM kols k
        LEFT JOIN vkpi_projects p ON p.kol_id = k.id AND COALESCE(p.stage_status,'') != 'deleted'
        LEFT JOIN vkpi_kol_claims c ON c.kol_id = k.id AND c.status='active'
        LEFT JOIN vkpi_sales_attributions sa ON sa.kol_id = k.id AND COALESCE(sa.revenue_cents, 0) > 0
        WHERE (
            p.id IS NOT NULL
            OR c.id IS NOT NULL
            OR sa.id IS NOT NULL
        )
          AND TRIM(COALESCE(k.contact_email,'')) = ''
          AND TRIM(COALESCE(k.contact_phone,'')) = ''
          AND TRIM(COALESCE(k.contact_links_json,'')) IN ('','[]','{{}}','null')
        {kol_contact_where}
        GROUP BY k.id, k.platform, k.channel_name, k.channel_url, k.contact_email,
                 k.contact_phone, k.contact_links_json, k.assigned_staff_id,
                 p.id, p.project_name, p.assigned_staff_id, c.staff_id
        ORDER BY COALESCE(SUM(sa.revenue_cents), 0) DESC, k.id DESC
        LIMIT ?
        """,
        (*kol_contact_params, max_items),
    )
    for item in rows:
        _append_issue(
            issues,
            issue_type="kol_missing_contact",
            severity="medium",
            title="红人已进入业务链路但没有联系方式",
            entity_type="kol",
            entity_id=item.get("id"),
            staff_id=item.get("project_staff_id") or item.get("claim_staff_id") or item.get("assigned_staff_id"),
            project_id=item.get("project_id"),
            kol_id=item.get("id"),
            detail=f"{item.get('platform')} · {item.get('channel_name') or item.get('channel_url')}",
            evidence=item,
        )
