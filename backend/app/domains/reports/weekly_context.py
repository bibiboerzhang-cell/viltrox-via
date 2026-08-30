"""Pure orchestration helpers for building weekly and monthly report context.

Runtime dependencies are supplied by the public ``reports`` facade.  This
keeps the leaf acyclic and preserves the facade's historical monkeypatch and
call-order surface without importing database-backed domain modules here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable


Callback = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class WeeklyContextDependencies:
    ensure_schema: Callback
    sanitize_filters: Callback
    report_spec_for: Callback
    period: Callback
    parse_moment: Callback
    report_scope_id: Callback
    is_current_report_date: Callback
    dashboard: Callback
    staff_kpi: Callback
    list_projects: Callback
    list_attributions: Callback
    list_costs: Callback
    list_alerts: Callback
    assert_project_access: Callback
    in_window: Callback
    int_or_none: Callback
    first_int: Callback
    localized: Callback
    money_cents: Callback
    metric_payload: Callback
    kpi_source_appendix: Callback
    utcnow: Callback
    staff_name: Callback
    metric_value_factory: Callback
    report_data_status: Callback
    data_status: Any


@dataclass(frozen=True, slots=True)
class RequestState:
    filters: dict[str, Any]
    period_days: int
    language: str
    selected_sections: set[str]
    report_spec: Any
    start: str
    end: str
    window_start: datetime | None
    window_end: datetime | None
    scoped_staff_id: int | None
    current_window: bool


@dataclass(slots=True)
class SourceState:
    summary: dict[str, Any]
    staff_window_supported: bool
    staff_kpi: dict[str, Any]
    project_rows: list[dict[str, Any]]
    attr_rows: list[dict[str, Any]]
    cost_rows: list[dict[str, Any]]
    alert_rows: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class FinancialState:
    sales_by_project: dict[int, int]
    cost_by_project: dict[int, int]
    total_sales: int
    approved_actual_cost_rows: list[dict[str, Any]]
    total_cost: int


@dataclass(frozen=True, slots=True)
class MetricState:
    total_views: int | None
    views_status: Any
    views_source_count: int | None
    new_kol: int | None
    published: int | None
    staff_metric_status: Any
    staff_source_count: int | None
    active_projects: int
    funnel_counts: dict[str, int]


def _prepare_request(
    period_days: int | None,
    *,
    staff: dict[str, Any] | None,
    filters: dict[str, Any] | None,
    deps: WeeklyContextDependencies,
) -> RequestState:
    deps.ensure_schema()
    raw_filters = dict(filters or {})
    if "period_days" not in raw_filters and period_days is not None:
        raw_filters["period_days"] = period_days
    safe_filters = deps.sanitize_filters(raw_filters)
    normalized_period_days = int(safe_filters["period_days"])
    language = str(safe_filters["language"])
    selected_sections = set(safe_filters["sections"])
    report_spec = deps.report_spec_for(str(safe_filters["report_type"]))
    start, end = deps.period(
        normalized_period_days,
        date_from=str(safe_filters["date_from"]),
        date_to=str(safe_filters["date_to"]),
    )
    window_start = deps.parse_moment(start)
    window_end = deps.parse_moment(end)
    scoped_staff_id = deps.report_scope_id(safe_filters, staff)
    current_window = deps.is_current_report_date(safe_filters["date_to"])
    return RequestState(
        filters=safe_filters,
        period_days=normalized_period_days,
        language=language,
        selected_sections=selected_sections,
        report_spec=report_spec,
        start=start,
        end=end,
        window_start=window_start,
        window_end=window_end,
        scoped_staff_id=scoped_staff_id,
        current_window=current_window,
    )


def _load_sources(
    request: RequestState,
    *,
    staff: dict[str, Any] | None,
    deps: WeeklyContextDependencies,
) -> SourceState:
    dashboard = (
        deps.dashboard(window_days=request.period_days)
        if request.current_window and not request.scoped_staff_id
        else {"summary": {}}
    )
    summary = dashboard.get("summary") or {}
    staff_window = (
        "7d"
        if request.period_days == 7
        else "30d"
        if request.period_days == 30
        else ""
    )
    staff_window_supported = request.current_window and bool(staff_window)
    staff_kpi = (
        deps.staff_kpi(
            window=staff_window,
            staff_id=request.scoped_staff_id,
        )
        if staff_window_supported
        else {"rows": []}
    )
    project_rows = deps.list_projects(
        limit=200,
        staff=staff,
        staff_id_filter=request.scoped_staff_id,
    ).get("projects") or []
    attr_rows = [
        row
        for row in (
            deps.list_attributions(
                limit=500,
                staff_id=request.scoped_staff_id,
                staff=staff,
            ).get("attributions")
            or []
        )
        if deps.in_window(
            row.get("occurred_at"), request.window_start, request.window_end
        )
        and int(row.get("is_verified_business_truth") or 0) == 1
    ]
    cost_rows = [
        row
        for row in (
            deps.list_costs(
                limit=500,
                staff_id=request.scoped_staff_id,
                staff=staff,
            ).get("costs")
            or []
        )
        if deps.in_window(
            row.get("incurred_at"), request.window_start, request.window_end
        )
        and int(row.get("is_approved_actual") or 0) == 1
    ]
    alert_rows = deps.list_alerts(
        status="open",
        limit=100,
        staff=staff,
        staff_id=request.scoped_staff_id,
    ).get("alerts") or []
    project_id = deps.int_or_none(request.filters.get("project_id"))
    if project_id:
        deps.assert_project_access(project_id, staff)
        project_rows = [
            row
            for row in project_rows
            if deps.int_or_none(row.get("id")) == project_id
        ]
        attr_rows = [
            row
            for row in attr_rows
            if deps.int_or_none(row.get("project_id")) == project_id
        ]
        cost_rows = [
            row
            for row in cost_rows
            if deps.int_or_none(row.get("project_id")) == project_id
        ]
    return SourceState(
        summary=summary,
        staff_window_supported=staff_window_supported,
        staff_kpi=staff_kpi,
        project_rows=project_rows,
        attr_rows=attr_rows,
        cost_rows=cost_rows,
        alert_rows=alert_rows,
    )


def _aggregate_financials(sources: SourceState) -> FinancialState:
    sales_by_project: dict[int, int] = {}
    for row in sources.attr_rows:
        project_id = int(row.get("project_id") or 0)
        sales_by_project[project_id] = sales_by_project.get(project_id, 0) + int(
            row.get("revenue_cents") or 0
        )
    cost_by_project: dict[int, int] = {}
    for row in sources.cost_rows:
        project_id = int(row.get("project_id") or 0)
        cost_by_project[project_id] = cost_by_project.get(project_id, 0) + int(
            row.get("amount_cents") or 0
        )
    total_sales = sum(
        int(row.get("revenue_cents") or 0) for row in sources.attr_rows
    )
    approved_actual_cost_rows = list(sources.cost_rows)
    total_cost = sum(
        int(row.get("amount_cents") or 0) for row in approved_actual_cost_rows
    )
    return FinancialState(
        sales_by_project=sales_by_project,
        cost_by_project=cost_by_project,
        total_sales=total_sales,
        approved_actual_cost_rows=approved_actual_cost_rows,
        total_cost=total_cost,
    )


def _staff_kpi_rows(staff_kpi: dict[str, Any]) -> list[dict[str, Any]]:
    return (
        staff_kpi.get("rows")
        if isinstance(staff_kpi.get("rows"), list)
        else []
    )


def _resolve_view_metrics(
    request: RequestState,
    sources: SourceState,
    staff_kpi_rows: list[dict[str, Any]],
    deps: WeeklyContextDependencies,
) -> tuple[int | None, Any, int | None]:
    total_views = deps.first_int(
        sources.summary, ("total_views", "views", "impressions")
    )
    views_status = (
        deps.data_status.REAL
        if total_views is not None
        else deps.data_status.AWAITING_SOURCE
    )
    views_source_count: int | None = None
    if request.scoped_staff_id and sources.staff_window_supported:
        if not staff_kpi_rows:
            total_views = 0
            views_status = deps.data_status.REAL
            views_source_count = 0
        else:
            view_values = [
                value
                for row in staff_kpi_rows
                if (value := deps.int_or_none(row.get("content_views"))) is not None
            ]
            views_source_count = len(view_values)
            if view_values:
                total_views = sum(view_values)
                views_status = (
                    deps.data_status.REAL
                    if len(view_values) == len(staff_kpi_rows)
                    else deps.data_status.PARTIAL
                )
            else:
                total_views = None
                views_status = deps.data_status.AWAITING_SOURCE
    elif not request.current_window:
        total_views = None
        views_status = deps.data_status.AWAITING_SOURCE
    return total_views, views_status, views_source_count


def _resolve_staff_metrics(
    sources: SourceState,
    staff_kpi_rows: list[dict[str, Any]],
    deps: WeeklyContextDependencies,
) -> tuple[int | None, int | None, Any, int | None]:
    if sources.staff_window_supported:
        new_kol: int | None = sum(
            int(row.get("kol_claims") or 0) for row in staff_kpi_rows
        )
        published: int | None = sum(
            int(row.get("published") or 0) for row in staff_kpi_rows
        )
        staff_metric_status = deps.data_status.REAL
        staff_source_count: int | None = len(staff_kpi_rows)
    else:
        new_kol = None
        published = None
        staff_metric_status = deps.data_status.AWAITING_SOURCE
        staff_source_count = None
    return new_kol, published, staff_metric_status, staff_source_count


def _project_metrics(
    project_rows: list[dict[str, Any]],
) -> tuple[int, dict[str, int]]:
    active_projects = len(
        [
            row
            for row in project_rows
            if str(row.get("stage") or "")
            not in {"closed", "cancelled", "lost", "released"}
        ]
    )
    funnel_counts: dict[str, int] = {}
    for row in project_rows:
        stage = str(row.get("stage") or "unknown")
        funnel_counts[stage] = funnel_counts.get(stage, 0) + 1
    return active_projects, funnel_counts


def _resolve_metrics(
    request: RequestState,
    sources: SourceState,
    staff_kpi_rows: list[dict[str, Any]],
    deps: WeeklyContextDependencies,
) -> MetricState:
    total_views, views_status, views_source_count = _resolve_view_metrics(
        request, sources, staff_kpi_rows, deps
    )
    new_kol, published, staff_metric_status, staff_source_count = (
        _resolve_staff_metrics(sources, staff_kpi_rows, deps)
    )
    active_projects, funnel_counts = _project_metrics(sources.project_rows)
    return MetricState(
        total_views=total_views,
        views_status=views_status,
        views_source_count=views_source_count,
        new_kol=new_kol,
        published=published,
        staff_metric_status=staff_metric_status,
        staff_source_count=staff_source_count,
        active_projects=active_projects,
        funnel_counts=funnel_counts,
    )


def _build_staff_rows(
    rows: list[dict[str, Any]],
    *,
    language: str,
    deps: WeeklyContextDependencies,
) -> list[dict[str, Any]]:
    staff_rows: list[dict[str, Any]] = []
    for row in rows:
        staff_sales = deps.first_int(row, ("gmv_cents", "revenue_cents"))
        staff_cost = deps.int_or_none(row.get("cost_cents"))
        staff_rows.append(
            {
                "name": str(
                    row.get("staff_name")
                    or row.get("name")
                    or row.get("staff_id")
                    or deps.localized(language, "员工", "Staff")
                ),
                "kol_claims": int(row.get("kol_claims") or 0),
                "published": int(row.get("published") or 0),
                "sales": deps.money_cents(staff_sales, language=language),
                "cost": deps.money_cents(staff_cost, language=language),
                "projects": int(
                    row.get("active_projects") or row.get("project_count") or 0
                ),
            }
        )
    return staff_rows


def _build_project_context(
    sources: SourceState,
    financials: FinancialState,
    *,
    language: str,
    deps: WeeklyContextDependencies,
) -> list[dict[str, Any]]:
    project_context: list[dict[str, Any]] = []
    for row in sources.project_rows[:80]:
        project_id = int(row.get("id") or 0)
        project_context.append(
            {
                "project_name": str(
                    row.get("project_name")
                    or row.get("project_uid")
                    or deps.localized(language, "项目", "Project")
                ),
                "kol_name": str(row.get("kol_name") or row.get("kol_id") or "-"),
                "stage": str(row.get("stage") or "-"),
                "staff_name": str(
                    row.get("staff_name") or row.get("assigned_staff_id") or "-"
                ),
                "sales": deps.money_cents(
                    financials.sales_by_project.get(project_id, 0),
                    language=language,
                ),
                "cost": deps.money_cents(
                    financials.cost_by_project.get(project_id, 0),
                    language=language,
                ),
                "updated_at": str(row.get("updated_at") or "-"),
            }
        )
    return project_context


def _metric_values(
    request: RequestState,
    sources: SourceState,
    financials: FinancialState,
    metrics: MetricState,
    deps: WeeklyContextDependencies,
) -> list[Any]:
    language = request.language
    report_spec = request.report_spec
    return [
        deps.metric_value_factory(
            report_spec.metric("views"),
            metrics.total_views,
            metrics.views_status,
            source_count=metrics.views_source_count,
            note=deps.localized(
                language,
                "已抓取内容统计"
                if metrics.total_views is not None
                else "等待可靠播放量来源",
                "Captured content statistics"
                if metrics.total_views is not None
                else "Awaiting a reliable views source",
            ),
        ),
        deps.metric_value_factory(
            report_spec.metric("sales_cents"),
            financials.total_sales,
            deps.data_status.REAL,
            source_count=len(sources.attr_rows),
            note=deps.localized(
                language, "Shopify/Amazon 归因", "Shopify/Amazon attribution"
            ),
        ),
        deps.metric_value_factory(
            report_spec.metric("cost_cents"),
            financials.total_cost,
            deps.data_status.REAL,
            source_count=len(financials.approved_actual_cost_rows),
            note=deps.localized(language, "已审批实际成本", "Approved actual costs"),
        ),
        deps.metric_value_factory(
            report_spec.metric("new_kol"),
            metrics.new_kol,
            metrics.staff_metric_status,
            source_count=metrics.staff_source_count,
            note=deps.localized(
                language,
                "Claim 统计"
                if metrics.new_kol is not None
                else "所选日期范围暂无可用聚合",
                "Claim aggregation"
                if metrics.new_kol is not None
                else "Aggregation unavailable for the selected dates",
            ),
        ),
        deps.metric_value_factory(
            report_spec.metric("published_content"),
            metrics.published,
            metrics.staff_metric_status,
            source_count=metrics.staff_source_count,
            note=deps.localized(
                language,
                "项目阶段事件"
                if metrics.published is not None
                else "所选日期范围暂无可用聚合",
                "Project stage events"
                if metrics.published is not None
                else "Aggregation unavailable for the selected dates",
            ),
        ),
        deps.metric_value_factory(
            report_spec.metric("active_projects"),
            metrics.active_projects,
            deps.data_status.REAL,
            source_count=len(sources.project_rows),
            note=deps.localized(language, "未关闭项目", "Current non-closed projects"),
        ),
    ]


def _summary_copy(
    request: RequestState,
    financials: FinancialState,
    metrics: MetricState,
    deps: WeeklyContextDependencies,
) -> tuple[str, str]:
    language = request.language
    unknown = deps.localized(language, "未知", "Unknown")
    views_text = (
        unknown if metrics.total_views is None else f"{metrics.total_views:,}"
    )
    new_kol_text = unknown if metrics.new_kol is None else f"{metrics.new_kol:,}"
    published_text = (
        unknown if metrics.published is None else f"{metrics.published:,}"
    )
    if language == "en":
        summary_text = (
            f"Verified sales are {deps.money_cents(financials.total_sales, language=language)} and cost is "
            f"{deps.money_cents(financials.total_cost, language=language)} for the selected period. "
            f"Captured views: {views_text}; new KOLs: {new_kol_text}; published content: "
            f"{published_text}; active projects: {metrics.active_projects}. Unknown values remain unknown."
        )
        period_label = f"{request.filters['date_from']} to {request.filters['date_to']}"
    else:
        summary_text = (
            f"当前周期确认销售额为 {deps.money_cents(financials.total_sales)}，"
            f"成本为 {deps.money_cents(financials.total_cost)}，"
            f"已抓取播放量为 {views_text}。新增 KOL {new_kol_text} 个，"
            f"已发布内容 {published_text} 条，进行中项目 {metrics.active_projects} 个。"
            "成本口径为：发货自动计入镜头成本，员工只登记快递费和推广费用。"
        )
        period_label = f"{request.filters['date_from']} 至 {request.filters['date_to']}"
    return summary_text, period_label


def _alert_payloads(
    rows: list[dict[str, Any]],
    *,
    language: str,
    deps: WeeklyContextDependencies,
) -> list[dict[str, str]]:
    return [
        {
            "title": str(
                row.get("title")
                or row.get("alert_type")
                or deps.localized(language, "提醒", "Alert")
            ),
            "description": str(row.get("description") or row.get("message") or ""),
        }
        for row in rows
    ]


def _build_context_payload(
    request: RequestState,
    sources: SourceState,
    financials: FinancialState,
    metrics: MetricState,
    *,
    staff: dict[str, Any] | None,
    report_uid: str,
    staff_rows: list[dict[str, Any]],
    project_context: list[dict[str, Any]],
    metric_values: list[Any],
    kpis: list[dict[str, Any]],
    summary_text: str,
    period_label: str,
    deps: WeeklyContextDependencies,
) -> dict[str, Any]:
    include_summary = "summary" in request.selected_sections
    include_kpis = "kpiOverview" in request.selected_sections
    include_projects = "projects" in request.selected_sections
    include_ledger = "ledger" in request.selected_sections
    include_risks = "risks" in request.selected_sections
    totals = {
        "sales_cents": financials.total_sales,
        "cost_cents": financials.total_cost,
        "views": metrics.total_views,
        "new_kol": metrics.new_kol,
        "published": metrics.published,
        "active_projects": metrics.active_projects,
    }
    return {
        "title": request.report_spec.title_for(request.language),
        "report_type": request.report_spec.report_type,
        "report_spec": request.report_spec.as_dict(language=request.language),
        "data_status": deps.report_data_status(metric_values).value,
        "metric_statuses": {
            metric.spec.key: metric.data_status.value for metric in metric_values
        },
        "report_uid": report_uid,
        "period_label": period_label,
        "period_days": request.period_days,
        "period_start": request.start,
        "period_end": request.end,
        "generated_at": deps.utcnow(),
        "watermark_user": deps.staff_name(staff),
        "language": request.language,
        "format": request.filters["format"],
        "sections": list(request.filters["sections"]),
        "scope": "staff" if request.scoped_staff_id else "all",
        "scope_id": request.scoped_staff_id,
        "summary_text": summary_text if include_summary else "",
        "kpis": kpis if include_kpis else [],
        "funnel": (
            [
                {"stage": key, "count": value}
                for key, value in sorted(metrics.funnel_counts.items())
            ]
            if include_projects
            else []
        ),
        "staff_rows": staff_rows if include_ledger else [],
        "projects": project_context if include_projects else [],
        "alerts": (
            _alert_payloads(
                sources.alert_rows,
                language=request.language,
                deps=deps,
            )
            if include_risks
            else []
        ),
        "kpi_appendix": (
            deps.kpi_source_appendix(
                str(request.filters["date_from"]),
                str(request.filters["date_to"]),
                scoped_staff_id=request.scoped_staff_id,
            )
            if include_ledger
            else {}
        ),
        "metric_run_id": None,
        "filters": dict(request.filters),
        "request": dict(request.filters),
        "totals": totals if include_summary or include_kpis else {},
    }


def build_weekly_context_impl(
    period_days: int | None = None,
    *,
    staff: dict[str, Any] | None = None,
    filters: dict[str, Any] | None = None,
    report_uid: str = "",
    deps: WeeklyContextDependencies,
) -> dict[str, Any]:
    request = _prepare_request(
        period_days,
        staff=staff,
        filters=filters,
        deps=deps,
    )
    sources = _load_sources(request, staff=staff, deps=deps)
    financials = _aggregate_financials(sources)
    staff_kpi_rows = _staff_kpi_rows(sources.staff_kpi)
    metrics = _resolve_metrics(request, sources, staff_kpi_rows, deps)
    staff_rows = _build_staff_rows(
        staff_kpi_rows,
        language=request.language,
        deps=deps,
    )
    project_context = _build_project_context(
        sources,
        financials,
        language=request.language,
        deps=deps,
    )
    metric_values = _metric_values(request, sources, financials, metrics, deps)
    kpis = [
        deps.metric_payload(metric, language=request.language)
        for metric in metric_values
    ]
    summary_text, period_label = _summary_copy(request, financials, metrics, deps)
    return _build_context_payload(
        request,
        sources,
        financials,
        metrics,
        staff=staff,
        report_uid=report_uid,
        staff_rows=staff_rows,
        project_context=project_context,
        metric_values=metric_values,
        kpis=kpis,
        summary_text=summary_text,
        period_label=period_label,
        deps=deps,
    )


__all__ = ["WeeklyContextDependencies", "build_weekly_context_impl"]
