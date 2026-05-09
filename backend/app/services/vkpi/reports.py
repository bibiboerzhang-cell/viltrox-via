"""V-KPI structured report generation."""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timedelta
from typing import Any

from app.db.connection import get_conn
from app.services.vkpi import alerts, attribution, costs, decision_engine, kpi_ledger, llm_gateway, metric_lineage, pdf_renderer, scope, workflow
from app.services.vkpi.schema import ensure_vkpi_schema
from app.services.vkpi.schema_lineage import ensure_vkpi_lineage_schema
from app.services.vkpi.schema_reports import ensure_vkpi_reports_schema


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _money_cents(value: Any) -> str:
    try:
        cents = int(value or 0)
    except (TypeError, ValueError):
        cents = 0
    return f"${cents / 100:,.0f}"


def _uid(prefix: str) -> str:
    return f"{prefix}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"


def _staff_name(staff: dict[str, Any] | None) -> str:
    if not staff:
        return "system"
    return str(staff.get("name") or staff.get("email") or staff.get("id") or "staff")


def _load_json(value: Any) -> Any:
    if not value:
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return {}


def _metric_label(metric_key: str) -> str:
    labels = {
        "views": "播放量",
        "gmv": "本周销售额",
        "cost": "成本",
        "net_contribution": "销售额减成本",
        "roi": "ROI",
        "new_kol": "新增 KOL",
        "published_content": "已发布内容",
        "valid_clicks": "有效点击",
        "active_projects": "进行中项目",
        "staff_kpi": "员工 KPI",
        "product_roi": "产品表现",
        "alerts": "提醒",
    }
    return labels.get(metric_key, metric_key)


def _format_metric_value(metric_key: str, value: Any, unit: str = "", currency: str = "") -> str:
    try:
        numeric = float(value or 0)
    except (TypeError, ValueError):
        numeric = 0.0
    if currency == "USD" or metric_key in {"gmv", "cost", "net_contribution"}:
        return _money_cents(numeric)
    if metric_key == "roi":
        return f"{numeric:.2f}x"
    if unit == "count" or metric_key in {"views", "new_kol", "published_content", "valid_clicks", "active_projects"}:
        return f"{int(numeric):,}"
    return f"{numeric:,.2f}".rstrip("0").rstrip(".")


def _format_kpis_for_prompt(kpis: list[dict[str, Any]]) -> str:
    if not kpis:
        return "- 暂无 KPI 数据"
    return "\n".join(
        f"- {str(item.get('label') or '-')}: {str(item.get('value') or '-')} ({str(item.get('note') or '无备注')})"
        for item in kpis[:12]
    )


def _format_funnel_for_prompt(funnel: list[dict[str, Any]]) -> str:
    if not funnel:
        return "- 暂无漏斗数据"
    return "\n".join(
        f"- {str(item.get('stage') or '-')}: {int(item.get('count') or 0)}"
        for item in funnel[:16]
    )


def _format_staff_for_prompt(staff_rows: list[dict[str, Any]]) -> str:
    if not staff_rows:
        return "- 暂无员工表现数据"
    lines: list[str] = []
    for item in staff_rows[:5]:
        lines.append(
            "- "
            f"{str(item.get('name') or '员工')}: "
            f"销售 {str(item.get('sales') or '$0')}, "
            f"成本 {str(item.get('cost') or '$0')}, "
            f"新增 KOL {int(item.get('kol_claims') or 0)}, "
            f"发布 {int(item.get('published') or 0)}, "
            f"项目 {int(item.get('projects') or 0)}"
        )
    return "\n".join(lines)


def _format_alerts_for_prompt(alert_rows: list[dict[str, Any]]) -> str:
    if not alert_rows:
        return "- 当前无未处理提醒"
    return "\n".join(
        f"- {str(item.get('title') or '提醒')}: {str(item.get('description') or '')[:140]}"
        for item in alert_rows[:6]
    )


def _build_weekly_prompt(context: dict[str, Any]) -> str:
    totals = context.get("totals") if isinstance(context.get("totals"), dict) else {}
    return f"""你是 Viltrox 内部营销分析师。请基于真实系统数据写一段中文周报总结。

时间范围: {context.get('period_label') or ''}

KPI 概览:
{_format_kpis_for_prompt(context.get('kpis') or [])}

漏斗:
{_format_funnel_for_prompt(context.get('funnel') or [])}

员工表现 Top 5:
{_format_staff_for_prompt(context.get('staff_rows') or [])}

未处理提醒:
{_format_alerts_for_prompt(context.get('alerts') or [])}

汇总口径:
- 销售额 cents: {totals.get('sales_cents', 0)}
- 成本 cents: {totals.get('cost_cents', 0)}
- 播放量: {totals.get('views', 0)}
- 新增 KOL: {totals.get('new_kol', 0)}
- 已发布内容: {totals.get('published', 0)}
- 进行中项目: {totals.get('active_projects', 0)}

要求:
- 200 字以内
- 只基于上面的真实数据,不要编造数据
- 先讲本周结论,再指出风险或下一步动作
- 中文,专业、直接,不要写成广告文案
"""


def _generate_ai_summary(context: dict[str, Any], *, staff: dict[str, Any] | None = None) -> str:
    prompt = _build_weekly_prompt(context)
    if str(os.getenv("VKPI_WEEKLY_SUMMARY_AI_DISABLED") or "").strip().lower() in {"1", "true", "yes", "on"}:
        llm_gateway.record_call(
            provider="rule_v0",
            model="rule_v0",
            purpose="vkpi_weekly_summary",
            prompt=prompt,
            status="disabled",
            fallback_used=True,
            metadata={"reason": "VKPI_WEEKLY_SUMMARY_AI_DISABLED", "period_label": context.get("period_label", "")},
            staff=staff,
        )
        return ""

    result = llm_gateway.invoke(
        prompt,
        purpose="vkpi_weekly_summary",
        max_output_tokens=1024,
        preferred_provider=(os.getenv("VKPI_WEEKLY_SUMMARY_PROVIDER") or "anthropic"),
        metadata={"period_label": context.get("period_label", "")},
        staff=staff,
    )
    if str(result.get("status") or "") == "success":
        text = str(result.get("text") or "").strip()
        if not text:
            return ""
        return text[:1200].strip()
    return ""


def _compact_snapshot(snapshot: Any) -> str:
    data = snapshot if isinstance(snapshot, dict) else _load_json(snapshot)
    if not isinstance(data, dict) or not data:
        return "-"
    preferred = [
        "source_ref",
        "order_name",
        "project_name",
        "kol_name",
        "staff_name",
        "product_sku",
        "post_url",
        "destination_url",
        "title",
        "status",
    ]
    parts: list[str] = []
    for key in preferred:
        value = data.get(key)
        if value not in (None, ""):
            parts.append(f"{key}: {value}")
        if len(parts) >= 3:
            break
    if parts:
        return " · ".join(parts)
    return json.dumps(data, ensure_ascii=False, default=str)[:160]


def _source_appendix(metric_run_id: int | None, *, limit_per_metric: int = 8) -> list[dict[str, Any]]:
    if not metric_run_id:
        return []
    ensure_vkpi_lineage_schema()
    conn = get_conn()
    values = conn.execute(
        """
        SELECT id, metric_key, value_numeric, currency, unit, source_count
        FROM vkpi_metric_values
        WHERE run_id=?
        ORDER BY
            CASE metric_key
                WHEN 'views' THEN 1
                WHEN 'gmv' THEN 2
                WHEN 'cost' THEN 3
                WHEN 'net_contribution' THEN 4
                WHEN 'roi' THEN 5
                WHEN 'new_kol' THEN 6
                WHEN 'published_content' THEN 7
                WHEN 'valid_clicks' THEN 8
                ELSE 50
            END,
            metric_key
        """,
        (int(metric_run_id),),
    ).fetchall()
    appendix: list[dict[str, Any]] = []
    for value in values:
        value_data = dict(value)
        rows = conn.execute(
            """
            SELECT s.id, s.source_type, s.source_id, s.contribution_amount,
                   s.contribution_percent, s.evidence_type, s.evidence_ref,
                   s.project_id, s.kol_id, s.staff_id, s.occurred_at, s.snapshot_json,
                   p.project_name, p.project_uid,
                   k.channel_name AS kol_name, k.platform AS kol_platform,
                   u.name AS staff_name, u.email AS staff_email
            FROM vkpi_metric_sources s
            LEFT JOIN vkpi_projects p ON p.id = s.project_id
            LEFT JOIN kols k ON k.id = s.kol_id
            LEFT JOIN staff st ON st.id = s.staff_id
            LEFT JOIN users u ON u.id = st.user_id
            WHERE s.metric_value_id=?
            ORDER BY ABS(COALESCE(s.contribution_amount, 0)) DESC, s.occurred_at DESC, s.id DESC
            LIMIT ?
            """,
            (int(value_data["id"]), max(1, min(25, int(limit_per_metric or 8)))),
        ).fetchall()
        appendix.append({
            "metric_key": value_data.get("metric_key"),
            "metric_label": _metric_label(str(value_data.get("metric_key") or "")),
            "value": _format_metric_value(
                str(value_data.get("metric_key") or ""),
                value_data.get("value_numeric"),
                str(value_data.get("unit") or ""),
                str(value_data.get("currency") or ""),
            ),
            "source_count": int(value_data.get("source_count") or 0),
            "rows": [
                {
                    "source_type": str(row["source_type"] or "-"),
                    "source_id": row["source_id"],
                    "amount": _format_metric_value(
                        str(value_data.get("metric_key") or ""),
                        row["contribution_amount"],
                        str(value_data.get("unit") or ""),
                        str(value_data.get("currency") or ""),
                    ),
                    "percent": f"{float(row['contribution_percent'] or 0):.1f}%",
                    "project": row["project_name"] or row["project_uid"] or "-",
                    "kol": row["kol_name"] or "-",
                    "staff": row["staff_name"] or row["staff_email"] or "-",
                    "evidence": row["evidence_ref"] or row["evidence_type"] or "-",
                    "occurred_at": row["occurred_at"] or "-",
                    "snapshot": _compact_snapshot(row["snapshot_json"]),
                }
                for row in rows
            ],
        })
    return appendix


def _format_kpi_value(metric_key: str, value: Any) -> str:
    try:
        numeric = float(value or 0)
    except (TypeError, ValueError):
        numeric = 0.0
    if metric_key.endswith("_cents"):
        return _money_cents(numeric)
    if metric_key in {"roi", "net_roi", "recommendation_roi"}:
        return f"{numeric:.2f}x"
    return f"{numeric:,.2f}".rstrip("0").rstrip(".")


def _component_summary(components: Any, *, limit: int = 5) -> str:
    if not isinstance(components, list) or not components:
        return ""
    parts: list[str] = []
    for item in components[: max(1, int(limit or 5))]:
        if not isinstance(item, dict):
            continue
        label = item.get("metric_label") or item.get("metric_key") or "指标"
        parts.append(f"{label}: {_format_kpi_value('', item.get('contribution'))}")
    return " / ".join(parts)


def _kpi_source_appendix(start_date: str, *, scoped_staff_id: int | None = None, limit: int = 120) -> dict[str, Any]:
    ensure_vkpi_schema()
    conn = get_conn()
    where = "WHERE kl.ledger_date >= ?"
    params: list[Any] = [start_date[:10]]
    if scoped_staff_id:
        where += " AND kl.staff_id=?"
        params.append(int(scoped_staff_id))
    grouped = conn.execute(
        f"""
        SELECT kl.metric_key,
               COUNT(*) AS source_count,
               COALESCE(SUM(kl.metric_value), 0) AS total_value,
               MIN(kl.ledger_date) AS first_date,
               MAX(kl.ledger_date) AS last_date,
               MAX(kl.confidence) AS confidence
        FROM vkpi_kpi_ledger kl
        {where}
        GROUP BY kl.metric_key
        ORDER BY
            CASE
                WHEN kl.metric_key IN ('kpi_credit', 'workload_score') THEN 0
                WHEN substr(kl.metric_key, 1, 15)='recommendation_' THEN 1
                ELSE 2
            END,
            total_value DESC,
            kl.metric_key ASC
        LIMIT 80
        """,
        tuple(params),
    ).fetchall()
    source_rows = conn.execute(
        f"""
        SELECT kl.id, kl.ledger_date, kl.staff_id, kl.kol_id, kl.project_id,
               kl.metric_key, kl.metric_value, kl.source_type, kl.source_ref,
               kl.confidence, kl.metadata_json, kl.created_at,
               p.project_name, p.project_uid, p.product_sku,
               k.channel_name AS kol_name, k.platform AS kol_platform,
               u.name AS staff_name, u.email AS staff_email
        FROM vkpi_kpi_ledger kl
        LEFT JOIN vkpi_projects p ON p.id = kl.project_id
        LEFT JOIN kols k ON k.id = kl.kol_id
        LEFT JOIN staff st ON st.id = kl.staff_id
        LEFT JOIN users u ON u.id = st.user_id
        {where}
        ORDER BY
            CASE
                WHEN kl.metric_key IN ('kpi_credit', 'workload_score') THEN 0
                WHEN substr(kl.metric_key, 1, 15)='recommendation_' THEN 1
                ELSE 2
            END,
            kl.ledger_date DESC,
            kl.id DESC
        LIMIT ?
        """,
        (*params, max(1, min(300, int(limit or 120)))),
    ).fetchall()
    grouped_rows: list[dict[str, Any]] = []
    for row in grouped:
        item = dict(row)
        key = str(item.get("metric_key") or "")
        item["metric_label"] = kpi_ledger.METRIC_LABELS.get(key, key)
        item["formatted_total"] = _format_kpi_value(key, item.get("total_value"))
        item["is_recommendation_metric"] = key.startswith("recommendation_")
        grouped_rows.append(item)
    detail_rows: list[dict[str, Any]] = []
    for row in source_rows:
        item = dict(row)
        key = str(item.get("metric_key") or "")
        metadata = _load_json(item.get("metadata_json"))
        components = metadata.get("components") if isinstance(metadata, dict) else []
        detail_rows.append({
            "id": item.get("id"),
            "ledger_date": item.get("ledger_date"),
            "metric_key": key,
            "metric_label": kpi_ledger.METRIC_LABELS.get(key, key),
            "value": _format_kpi_value(key, item.get("metric_value")),
            "source_type": item.get("source_type") or "-",
            "source_ref": item.get("source_ref") or "-",
            "confidence": item.get("confidence") or "-",
            "project": item.get("project_name") or item.get("project_uid") or "-",
            "kol": item.get("kol_name") or "-",
            "staff": item.get("staff_name") or item.get("staff_email") or "-",
            "formula": metadata.get("formula") if isinstance(metadata, dict) else "",
            "component_summary": _component_summary(components),
            "recommendation_id": metadata.get("recommendation_id") if isinstance(metadata, dict) else None,
            "outcome_id": metadata.get("outcome_id") if isinstance(metadata, dict) else None,
            "launch_id": metadata.get("launch_id") if isinstance(metadata, dict) else None,
        })
    return {
        "start": start_date[:10],
        "staff_id": scoped_staff_id,
        "grouped": grouped_rows,
        "source_rows": detail_rows,
        "source_count": len(detail_rows),
    }


def _period(period_days: int) -> tuple[str, str]:
    end = datetime.utcnow()
    start = end - timedelta(days=max(1, int(period_days or 7)))
    return start.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ")


def _maybe_metric_run(period_days: int, staff: dict[str, Any] | None, scoped_staff_id: int | None) -> int | None:
    try:
        ensure_vkpi_lineage_schema()
        result = metric_lineage.generate_run(
            period_days=period_days,
            scope_type="staff" if scoped_staff_id else "all",
            scope_id=scoped_staff_id,
            trigger_source="weekly_report",
            generated_by_staff_id=workflow.staff_id(staff) or None,
            metadata={"source": "reports.generate_weekly_report"},
        )
        return int(result.get("run_id") or 0) or None
    except Exception:
        return None


def build_weekly_context(period_days: int = 7, *, staff: dict[str, Any] | None = None, filters: dict[str, Any] | None = None, report_uid: str = "") -> dict[str, Any]:
    ensure_vkpi_schema()
    filters = filters or {}
    start, end = _period(period_days)
    scoped_staff_id = scope.effective_staff_id(staff, filters.get("staff_id"))
    dashboard = decision_engine.dashboard(window_days=period_days) if not scoped_staff_id else {"summary": {}}
    summary = dashboard.get("summary") or {}
    staff_kpi = decision_engine.staff_kpi(window="week", staff_id=scoped_staff_id)
    project_rows = workflow.list_projects(limit=200, staff=staff, staff_id_filter=scoped_staff_id).get("projects") or []
    attr_rows = attribution.list_attributions(limit=500, staff_id=scoped_staff_id, staff=staff).get("attributions") or []
    cost_rows = costs.list_costs(limit=500, staff_id=scoped_staff_id, staff=staff).get("costs") or []
    alert_rows = alerts.list_alerts(status="open", limit=100, staff=staff, staff_id=scoped_staff_id).get("alerts") or []
    sales_by_project: dict[int, int] = {}
    for row in attr_rows:
        pid = int(row.get("project_id") or 0)
        sales_by_project[pid] = sales_by_project.get(pid, 0) + int(row.get("revenue_cents") or 0)
    cost_by_project: dict[int, int] = {}
    for row in cost_rows:
        if str(row.get("status") or "") == "void":
            continue
        pid = int(row.get("project_id") or 0)
        cost_by_project[pid] = cost_by_project.get(pid, 0) + int(row.get("amount_cents") or 0)
    total_sales = sum(int(row.get("revenue_cents") or 0) for row in attr_rows)
    total_cost = sum(int(row.get("amount_cents") or 0) for row in cost_rows if str(row.get("status") or "") != "void")
    total_views = int(summary.get("total_views") or summary.get("views") or summary.get("impressions") or 0)
    if scoped_staff_id:
        total_views = sum(int(row.get("content_views") or 0) for row in (staff_kpi.get("rows") or []))
    new_kol = sum(int(row.get("kol_claims") or 0) for row in (staff_kpi.get("rows") or []))
    published = sum(int(row.get("published") or 0) for row in (staff_kpi.get("rows") or []))
    active_projects = len([row for row in project_rows if str(row.get("stage") or "") not in {"closed", "cancelled", "lost", "released"}])
    funnel_counts: dict[str, int] = {}
    for row in project_rows:
        stage = str(row.get("stage") or "unknown")
        funnel_counts[stage] = funnel_counts.get(stage, 0) + 1
    staff_rows = []
    for row in staff_kpi.get("rows") or []:
        staff_rows.append({
            "name": str(row.get("staff_name") or row.get("name") or row.get("email") or row.get("staff_id") or "员工"),
            "kol_claims": int(row.get("kol_claims") or 0),
            "published": int(row.get("published") or 0),
            "sales": _money_cents(row.get("gmv_cents") or row.get("revenue_cents") or 0),
            "cost": _money_cents(row.get("cost_cents") or 0),
            "projects": int(row.get("active_projects") or row.get("project_count") or 0),
        })
    project_context = []
    for row in project_rows[:80]:
        pid = int(row.get("id") or 0)
        project_context.append({
            "project_name": str(row.get("project_name") or row.get("project_uid") or "项目"),
            "kol_name": str(row.get("kol_name") or row.get("kol_id") or "-"),
            "stage": str(row.get("stage") or "-"),
            "staff_name": str(row.get("staff_name") or row.get("assigned_staff_id") or "-"),
            "sales": _money_cents(sales_by_project.get(pid, 0)),
            "cost": _money_cents(cost_by_project.get(pid, 0)),
            "updated_at": str(row.get("updated_at") or "-"),
        })
    kpis = [
        {"label": "播放量", "value": f"{total_views:,}", "note": "已抓取内容统计"},
        {"label": "本周销售额", "value": _money_cents(total_sales), "note": "Shopify/Amazon 归因"},
        {"label": "成本", "value": _money_cents(total_cost), "note": "镜头自动 + 快递 + 推广"},
        {"label": "新增 KOL", "value": f"{new_kol:,}", "note": "Claim 统计"},
        {"label": "已发布内容", "value": f"{published:,}", "note": "项目阶段事件"},
        {"label": "进行中项目", "value": f"{active_projects:,}", "note": "未关闭项目"},
    ]
    summary_text = (
        f"当前周期确认销售额为 {_money_cents(total_sales)}，成本为 {_money_cents(total_cost)}，"
        f"已抓取播放量为 {total_views:,}。新增 KOL {new_kol} 个，已发布内容 {published} 条，"
        f"进行中项目 {active_projects} 个。成本口径为：发货自动计入镜头成本，员工只登记快递费和推广费用。"
    )
    return {
        "title": "Viltrox Marketing 周报",
        "report_uid": report_uid,
        "period_label": f"{start[:10]} 至 {end[:10]}",
        "period_days": period_days,
        "period_start": start,
        "period_end": end,
        "generated_at": _utcnow(),
        "watermark_user": _staff_name(staff),
        "summary_text": summary_text,
        "kpis": kpis,
        "funnel": [{"stage": k, "count": v} for k, v in sorted(funnel_counts.items(), key=lambda item: item[0])],
        "staff_rows": staff_rows,
        "projects": project_context,
        "alerts": [{"title": str(row.get("title") or row.get("alert_type") or "提醒"), "description": str(row.get("description") or row.get("message") or "")} for row in alert_rows],
        "kpi_appendix": _kpi_source_appendix(start[:10], scoped_staff_id=scoped_staff_id),
        "metric_run_id": None,
        "filters": filters or {},
        "totals": {"sales_cents": total_sales, "cost_cents": total_cost, "views": total_views, "new_kol": new_kol, "published": published, "active_projects": active_projects},
    }


def generate_weekly_report(*, period_days: int = 7, staff: dict[str, Any] | None = None, filters: dict[str, Any] | None = None, render_pdf: bool = True) -> dict[str, Any]:
    ensure_vkpi_reports_schema()
    actor_id = workflow.staff_id(staff) or None
    report_uid = _uid("weekly")
    context = build_weekly_context(period_days=period_days, staff=staff, filters=filters, report_uid=report_uid)
    scoped_staff_id = scope.effective_staff_id(staff, (filters or {}).get("staff_id"))
    metric_run_id = _maybe_metric_run(period_days, staff, scoped_staff_id)
    context["metric_run_id"] = metric_run_id
    context["source_appendix"] = _source_appendix(metric_run_id)
    ai_summary = _generate_ai_summary(context, staff=staff)
    if ai_summary:
        context["summary_text"] = ai_summary
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_report_runs
            (report_uid, report_type, period_start, period_end, scope_type, scope_id, metric_run_id,
             triggered_by_staff_id, triggered_at, status, summary_text, metadata_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (report_uid, "weekly", context["period_start"], context["period_end"], "staff" if scoped_staff_id else "all", scoped_staff_id, metric_run_id, actor_id, _utcnow(), "rendering" if render_pdf else "ready", context["summary_text"], _json(filters or {})),
    )
    report_row = conn.execute("SELECT id FROM vkpi_report_runs WHERE report_uid=?", (report_uid,)).fetchone()
    report_run_id = int(report_row["id"]) if report_row else 0
    conn.commit()
    file_info: dict[str, Any] = {}
    if render_pdf:
        try:
            filename = f"{report_uid}.pdf"
            stored = pdf_renderer.render_and_store_pdf(context, filename=filename)
            file_info = {key: value for key, value in stored.items() if key != "html"}
            download_url = f"/api/admin/vkpi/reports/files/{report_run_id}/download?format=pdf"
            conn.execute(
                """
                INSERT INTO vkpi_report_files
                    (report_run_id, file_format, file_path, file_size_bytes, download_url, sha256_hex, created_at)
                VALUES (?,?,?,?,?,?,?)
                """,
                (report_run_id, "pdf", file_info["file_path"], file_info["file_size_bytes"], download_url, file_info["sha256_hex"], _utcnow()),
            )
            conn.execute("UPDATE vkpi_report_runs SET status='ready' WHERE id=?", (report_run_id,))
            conn.commit()
            file_info["download_url"] = download_url
        except Exception as exc:
            conn.execute("UPDATE vkpi_report_runs SET status='failed', error_message=? WHERE id=?", (str(exc)[:500], report_run_id))
            conn.commit()
            raise
    return {"report_run_id": report_run_id, "report_uid": report_uid, "status": "ready", "download_url": file_info.get("download_url", ""), "downloadUrl": file_info.get("download_url", ""), "summary_text": context["summary_text"], "context": context, "file": file_info}


def list_reports(limit: int = 50, report_type: str = "", *, staff: dict[str, Any] | None = None, staff_id: int | None = None) -> dict[str, Any]:
    ensure_vkpi_reports_schema()
    where_parts: list[str] = []
    params: list[Any] = []
    if report_type:
        where_parts.append("report_type=?")
        params.append(report_type)
    scoped_staff_id = scope.effective_staff_id(staff, staff_id, domain="export")
    if scoped_staff_id:
        where_parts.append("(triggered_by_staff_id=? OR scope_id=?)")
        params.extend([int(scoped_staff_id), int(scoped_staff_id)])
    where = "WHERE " + " AND ".join(where_parts) if where_parts else ""
    rows = get_conn().execute(f"SELECT * FROM vkpi_report_runs {where} ORDER BY triggered_at DESC, id DESC LIMIT ?", (*params, max(1, min(200, int(limit or 50))))).fetchall()
    return {"reports": [dict(row) for row in rows]}


def report_file(report_run_id: int, file_format: str = "pdf", *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_reports_schema()
    report = get_conn().execute("SELECT * FROM vkpi_report_runs WHERE id=?", (int(report_run_id),)).fetchone()
    if not report:
        raise LookupError("report file not found")
    report_data = dict(report)
    triggered_by = int(report_data.get("triggered_by_staff_id") or 0)
    scope_id = int(report_data.get("scope_id") or 0)
    if triggered_by:
        scope.assert_staff_access(triggered_by, staff, domain="export")
    elif scope_id:
        scope.assert_staff_access(scope_id, staff, domain="export")
    elif not scope.can_view_all(staff, domain="export"):
        raise scope.ScopeDenied("report scope denied")
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM vkpi_report_files WHERE report_run_id=? AND file_format=? ORDER BY id DESC LIMIT 1",
        (int(report_run_id), file_format),
    ).fetchone()
    if not row:
        raise LookupError("report file not found")
    item = dict(row)
    actor_id = workflow.staff_id(staff) or None
    conn.execute(
        """
        UPDATE vkpi_report_files
        SET download_count=COALESCE(download_count, 0)+1,
            last_downloaded_at=?,
            last_downloaded_by_staff_id=?
        WHERE id=?
        """,
        (_utcnow(), actor_id, int(item["id"])),
    )
    conn.commit()
    item["download_count"] = int(item.get("download_count") or 0) + 1
    item["last_downloaded_by_staff_id"] = actor_id
    item["last_downloaded_at"] = _utcnow()
    return item
