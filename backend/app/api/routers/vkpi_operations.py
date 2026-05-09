"""V-KPI analytics, channel, campaign, budget, offboarding, and cron routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.services.vkpi import analytics, channels, cron, p5_selected, scope
from app.services.vkpi.workflow import staff_id as resolve_staff_id

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-operations"])


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


@router.post("/analytics/compare")
async def analytics_compare(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return await analytics.compare_products(body, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/analytics/monitor")
async def analytics_monitor(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return await analytics.monitor_product(body, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/analytics/runs")
def analytics_runs(limit: int = Query(default=50, ge=1, le=200), run_type: str = "", staff=Depends(require_tab("vkpi", "read"))):
    return analytics.list_runs(limit=limit, run_type=run_type)


@router.get("/analytics/runs/{run_id}")
def analytics_run(run_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        return analytics.get_run(run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/analytics/products")
def analytics_products(limit: int = Query(default=100, ge=1, le=300), staff=Depends(require_tab("vkpi", "read"))):
    return analytics.list_monitored_products(limit=limit)


@router.post("/analytics/products")
def analytics_product_upsert(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return analytics.upsert_monitored_product(body, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/analytics/products/{product_sku}")
def analytics_product_delete(product_sku: str, staff=Depends(require_tab("vkpi", "write"))):
    return analytics.delete_monitored_product(product_sku)


@router.get("/analytics/suggestions/overview")
def analytics_suggestions_overview(staff=Depends(require_tab("vkpi", "read"))):
    return analytics.suggestions_overview()


@router.get("/analytics/suggestions")
def analytics_suggestions(status: str = "new", product_sku: str = "", limit: int = Query(default=100, ge=1, le=500), staff=Depends(require_tab("vkpi", "read"))):
    return analytics.list_suggestions(status=status, product_sku=product_sku, limit=limit)


@router.get("/analytics/daily-digest")
def analytics_daily_digest(
    staff_id: int | None = None,
    date: str = "",
    limit: int = Query(default=100, ge=1, le=100),
    staff=Depends(require_tab("vkpi", "read")),
):
    requested_staff_id = staff_id if staff_id is not None else (resolve_staff_id(staff) or None)
    if staff_id is not None and not _is_manager_staff(staff):
        requested_staff_id = resolve_staff_id(staff) or None
    if not requested_staff_id:
        raise HTTPException(status_code=400, detail="staff_id required")
    return analytics.list_daily_staff_outreach_digest(
        int(requested_staff_id),
        target_date=date or None,
        limit=limit,
    )


@router.get("/analytics/daily-digest/status")
def analytics_daily_digest_status(
    date: str = "",
    limit: int = Query(default=100, ge=1, le=100),
    product_sku: str = "",
    staff=Depends(require_tab("vkpi", "read")),
):
    return analytics.daily_staff_outreach_digest_status(
        target_date=date or None,
        limit=limit,
        staff=staff,
        product_sku=product_sku,
    )


@router.post("/analytics/daily-digest/generate")
def analytics_daily_digest_generate(body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    payload = body or {}
    if payload.get("staff_id") and not _is_manager_staff(staff):
        raise HTTPException(status_code=403, detail="management permission required")
    return analytics.generate_daily_staff_outreach_digest(
        target_date=payload.get("date"),
        limit=int(payload.get("limit") or 100),
        staff=staff,
        product_sku=str(payload.get("product_sku") or ""),
    )


@router.post("/analytics/suggestions/{suggestion_id}/claim")
def analytics_suggestion_claim(suggestion_id: int, staff=Depends(require_tab("vkpi", "write"))):
    return analytics.claim_suggestion(suggestion_id, staff=staff)


@router.post("/analytics/suggestions/{suggestion_id}/create-project")
def analytics_suggestion_create_project(suggestion_id: int, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return analytics.create_project_from_suggestion(suggestion_id, body or {}, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/analytics/suggestions/{suggestion_id}/dismiss")
def analytics_suggestion_dismiss(suggestion_id: int, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    return analytics.dismiss_suggestion(suggestion_id, reason=str((body or {}).get("reason") or ""), staff=staff)


@router.get("/channels")
def list_channels(view_as_staff_id: int | None = None, limit: int = Query(default=100, ge=1, le=300), staff=Depends(require_tab("vkpi", "read"))):
    return channels.list_channels(staff=staff, view_as_staff_id=view_as_staff_id, limit=limit)


@router.post("/channels")
def bind_channel(body: dict, view_as_staff_id: int | None = None, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return channels.bind_channel(body, staff=staff, view_as_staff_id=view_as_staff_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/channels/{channel_id}")
def unbind_channel(channel_id: int, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return channels.unbind_channel(channel_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/channels/{channel_id}/sync-now")
def sync_channel(channel_id: int, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return channels.sync_now(channel_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/channels/{channel_id}/metrics")
def channel_metrics(channel_id: int, limit: int = Query(default=30, ge=1, le=365), staff=Depends(require_tab("vkpi", "read"))):
    try:
        return channels.metrics(channel_id, limit=limit, staff=staff)
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/channels/team-overview")
def channel_team_overview(staff=Depends(require_tab("vkpi", "admin"))):
    return channels.team_overview()


@router.get("/channels/team-detail/{staff_id}")
def channel_team_detail(staff_id: int, staff=Depends(require_tab("vkpi", "admin"))):
    return channels.team_detail(staff_id)


@router.get("/campaigns")
def campaigns(limit: int = Query(default=100, ge=1, le=300), status: str = "", staff=Depends(require_tab("vkpi", "read"))):
    _require_manager_staff(staff)
    return p5_selected.list_campaigns(limit=limit, status=status)


@router.post("/campaigns")
def create_campaign(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    _require_manager_staff(staff)
    try:
        return p5_selected.create_campaign(body, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/campaigns/{campaign_id}/projects")
def campaign_add_project(campaign_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    _require_manager_staff(staff)
    return p5_selected.add_project_to_campaign(campaign_id, int(body.get("project_id") or 0), staff=staff)


@router.get("/campaigns/{campaign_id}/progress")
def campaign_progress(campaign_id: int, staff=Depends(require_tab("vkpi", "read"))):
    _require_manager_staff(staff)
    try:
        return p5_selected.campaign_progress(campaign_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/budget-pools")
def budget_pools(limit: int = Query(default=100, ge=1, le=200), staff=Depends(require_tab("vkpi", "admin"))):
    return p5_selected.list_budget_pools(limit=limit)


@router.post("/budget-pools")
def create_budget_pool(body: dict, staff=Depends(require_tab("vkpi", "admin"))):
    try:
        return p5_selected.create_budget_pool(body, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/budget-pools/{pool_id}/allocate")
def allocate_budget(pool_id: int, body: dict, staff=Depends(require_tab("vkpi", "admin"))):
    try:
        return p5_selected.allocate_budget(pool_id, body, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/staff/{staff_id}/offboard/initiate")
def offboarding_initiate(staff_id: int, body: dict | None = None, staff=Depends(require_tab("vkpi", "admin"))):
    payload = body or {}
    return p5_selected.initiate_offboarding(staff_id, new_owner_staff_id=payload.get("new_owner_staff_id"), staff=staff)


@router.post("/offboarding/{run_id}/execute")
def offboarding_execute(run_id: int, body: dict | None = None, staff=Depends(require_tab("vkpi", "admin"))):
    return p5_selected.execute_offboarding(run_id, staff=staff, actions=(body or {}).get("actions"))


@router.get("/offboarding")
def offboarding_runs(limit: int = Query(default=100, ge=1, le=200), staff=Depends(require_tab("vkpi", "admin"))):
    return p5_selected.list_offboarding(limit=limit)


@router.post("/cron/{job_name}/run")
async def cron_run(job_name: str, body: dict | None = None, staff=Depends(require_tab("vkpi", "admin"))):
    payload = body or {}
    payload["staff"] = staff
    try:
        return await cron.run_job(job_name, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
