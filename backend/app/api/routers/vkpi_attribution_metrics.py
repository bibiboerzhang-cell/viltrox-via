"""V-KPI attribution, alerts, KPI ledger, and metric lineage routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from app.api.dependencies.perms import require_tab
from app.services.vkpi import alerts, attribution, audit, drilldown, integrations, kpi_ledger, metric_lineage, scope, workflow
from app.services.vkpi.workflow import staff_id as resolve_staff_id

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-attribution-metrics"])


def _is_manager_staff(staff: dict) -> bool:
    role = str(staff.get("role") or "").strip().lower()
    if int(staff.get("is_owner") or 0) == 1:
        return True
    return role in {"admin", "manager", "lead", "marketing_lead", "marketing_manager", "marketing-manager"}


def _require_manager_staff(staff: dict) -> None:
    if not _is_manager_staff(staff):
        raise HTTPException(status_code=403, detail="management permission required")


def _scope_403(exc: Exception) -> HTTPException:
    return HTTPException(status_code=403, detail=str(exc) or "scope denied")
@router.get("/attribution")
def list_attribution(
    project_id: int | None = None,
    staff_id: int | None = None,
    source: str = "",
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        return attribution.list_attributions(project_id=project_id, staff_id=staff_id, source=source, limit=limit, staff=staff)
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/attribution/confirmed")
def confirmed_attribution(
    project_id: int | None = None,
    staff_id: int | None = None,
    source: str = "",
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        rows = attribution.list_attributions(project_id=project_id, staff_id=staff_id, source=source, limit=limit, staff=staff).get("attributions", [])
        return {"attributions": [row for row in rows if str(row.get("confidence") or "").lower() == "confirmed"], "confidence": "confirmed"}
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/attribution/estimated")
def estimated_attribution(
    project_id: int | None = None,
    staff_id: int | None = None,
    source: str = "",
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        rows = attribution.list_attributions(project_id=project_id, staff_id=staff_id, source=source, limit=limit, staff=staff).get("attributions", [])
        estimated = {"estimated", "manual", "unmatched"}
        return {"attributions": [row for row in rows if str(row.get("confidence") or "").lower() in estimated], "confidence": "estimated"}
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/attribution")
def create_attribution(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return attribution.create_attribution(body, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/attribution/amazon/import")
def import_amazon(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return attribution.import_amazon(body, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/attribution/amazon")
def amazon_attributions(
    staff_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        return attribution.list_amazon_attributions(staff_id=staff_id, limit=limit, staff=staff)
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/attribution/amazon/summary")
def amazon_attribution_summary(
    staff_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        return attribution.amazon_summary(staff_id=staff_id, limit=limit, staff=staff)
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/attribution/amazon/{attribution_id}")
def amazon_attribution_detail(attribution_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        result = attribution.amazon_attribution_detail(int(attribution_id), staff=staff)
        row = result.get("attribution") or {}
        audit.log_sensitive_access(
            staff_id=resolve_staff_id(staff) or 0,
            action_type="view_financial",
            resource_type="amazon_attribution",
            resource_id=str(row.get("id") or attribution_id),
            page_path=f"/api/admin/vkpi/attribution/amazon/{attribution_id}",
            metadata={
                "source_platform": "amazon",
                "source_ref": row.get("source_ref") or "",
                "project_id": (result.get("project") or {}).get("id"),
                "kol_id": (result.get("kol") or {}).get("id"),
                "staff_id": (result.get("staff") or {}).get("id"),
            },
        )
        return result
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/attribution/amazon/upload")
async def upload_amazon_report(
    file: UploadFile = File(...),
    project_id: int | None = Form(default=None),
    staff_id: int | None = Form(default=None),
    amazon_tag: str = Form(default=""),
    asin: str = Form(default=""),
    marketplace: str = Form(default="US"),
    report_date: str = Form(default=""),
    staff=Depends(require_tab("vkpi", "write")),
):
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="amazon report too large")
    try:
        return integrations.import_amazon_report(
            content,
            file.filename or "amazon-report.csv",
            {
                "project_id": project_id,
                "staff_id": staff_id,
                "amazon_tag": amazon_tag,
                "asin": asin,
                "marketplace": marketplace,
                "report_date": report_date,
            },
            staff=staff,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/attribution/unmatched")
def unmatched_attribution(
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    return attribution.unmatched(limit=limit, staff=staff)


@router.get("/shopify/orders/{order_ref}")
def shopify_order_detail(order_ref: str, staff=Depends(require_tab("vkpi", "read"))):
    try:
        result = integrations.get_shopify_order_snapshot(order_ref, staff=staff)
        order = result.get("order_snapshot") or {}
        staff_id = resolve_staff_id(staff) or 0
        audit.log_sensitive_access(
            staff_id=staff_id,
            action_type="view_shopify_order",
            resource_type="shopify_order",
            resource_id=str(order.get("shopify_order_id") or order.get("id") or order_ref),
            page_path=f"/api/admin/vkpi/shopify/orders/{order_ref}",
            metadata={
                "order_ref": order_ref,
                "snapshot_id": order.get("id"),
                "order_name": order.get("order_name"),
                "landing_site": order.get("landing_site"),
                "source_row_count": result.get("source_row_count") or 0,
            },
        )
        audit.log_business_event(
            staff_id=staff_id,
            action_type="shopify_order_view",
            target_type="shopify_order",
            target_id=str(order.get("id") or order_ref),
            detail="view Shopify order evidence",
            metadata={
                "order_ref": order_ref,
                "order_name": order.get("order_name"),
                "landing_site": order.get("landing_site"),
                "source_row_count": result.get("source_row_count") or 0,
            },
        )
        return result
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/shopify/sync")
def shopify_sync(body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    _require_manager_staff(staff)
    return integrations.shopify_sync_status(body or {}, staff=staff, mode="sync")


@router.post("/shopify/backfill")
def shopify_backfill(body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    _require_manager_staff(staff)
    return integrations.shopify_sync_status(body or {}, staff=staff, mode="backfill")


@router.get("/alerts")
def list_alerts(
    status: str = "open",
    limit: int = Query(default=50, ge=1, le=200),
    staff=Depends(require_tab("vkpi", "read")),
):
    return alerts.list_alerts(status=status, limit=limit, staff=staff)


@router.post("/alerts/generate")
def generate_alerts(staff=Depends(require_tab("vkpi", "write"))):
    _require_manager_staff(staff)
    return alerts.generate_stalled_project_alerts()


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: int, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return alerts.resolve_alert(alert_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/kpi-ledger")
def list_kpi_ledger(
    staff_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    result = kpi_ledger.list_entries(limit=limit, staff_id=staff_id, staff=staff)
    actor_staff_id = resolve_staff_id(staff) or 0
    metadata = {
        "requested_staff_id": staff_id,
        "limit": limit,
        "row_count": len(result.get("entries") or []),
    }
    audit.log_sensitive_access(
        staff_id=actor_staff_id,
        action_type="view_kpi_ledger",
        resource_type="kpi_ledger",
        resource_id=str(staff_id or "scope"),
        page_path="/api/admin/vkpi/kpi-ledger",
        metadata=metadata,
    )
    audit.log_business_event(
        staff_id=actor_staff_id,
        action_type="kpi_ledger_view",
        target_type="kpi_ledger",
        target_id=str(staff_id or "scope"),
        detail="view KPI ledger rows",
        metadata=metadata,
    )
    return result


@router.post("/rollups/run-now")
def run_rollup(body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    _require_manager_staff(staff)
    payload = body or {}
    return kpi_ledger.generate_daily_rollup(
        payload.get("ledger_date"),
        staff_id=payload.get("staff_id"),
        actor_staff=staff,
    )


@router.post("/lineage/runs")
def lineage_create_run(body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    payload = body or {}
    requested_scope_type = str(payload.get("scope_type") or "all")
    requested_scope_id = payload.get("scope_id")
    if not _is_manager_staff(staff):
        requested_scope_type = "staff"
        requested_scope_id = resolve_staff_id(staff) or None
    try:
        return metric_lineage.generate_run(
            period_days=int(payload.get("period_days") or 30),
            scope_type=requested_scope_type,
            scope_id=requested_scope_id,
            trigger_source=str(payload.get("trigger_source") or "dashboard"),
            generated_by_staff_id=workflow.staff_id(staff) or None,
            metrics=payload.get("metrics"),
            metadata=payload.get("metadata"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/lineage/runs")
def lineage_list_runs(
    trigger_source: str | None = None,
    scope_type: str | None = None,
    limit: int = Query(default=20, ge=1, le=200),
    staff=Depends(require_tab("vkpi", "read")),
):
    return metric_lineage.list_runs(trigger_source=trigger_source, scope_type=scope_type, limit=limit, staff=staff)


@router.get("/lineage/runs/{run_id}")
def lineage_get_run(run_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        return metric_lineage.get_run(int(run_id), staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/lineage/values/{metric_value_id}/drilldown")
def lineage_drilldown_value(
    metric_value_id: int,
    project_id: int | None = None,
    kol_id: int | None = None,
    staff_id: int | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        return drilldown.drilldown_value(
            int(metric_value_id),
            limit=limit,
            project_id=project_id,
            kol_id=kol_id,
            staff_id=staff_id,
            staff=staff,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/lineage/metrics/{metric_key}/drilldown")
def lineage_drilldown_metric(
    metric_key: str,
    scope_type: str = "all",
    scope_id: int | None = None,
    project_id: int | None = None,
    kol_id: int | None = None,
    staff_id: int | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        if not _is_manager_staff(staff):
            scope_type = "staff"
            scope_id = resolve_staff_id(staff) or None
        return drilldown.drilldown_latest(
            metric_key,
            scope_type=scope_type,
            scope_id=scope_id,
            project_id=project_id,
            kol_id=kol_id,
            staff_id=staff_id,
            limit=limit,
            staff=staff,
        )
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
