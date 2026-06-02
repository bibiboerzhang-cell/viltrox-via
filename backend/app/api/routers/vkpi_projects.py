"""V-KPI project workflow routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.dependencies.perms import require_tab
from app.core.security import get_current_user
from app.domains import costs
from app.domains.access import scope
from app.domains.analysis.cache_repo import get_analysis_cache_entry
from app.domains.projects import workflow

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-projects"])


def _scope_403(exc: Exception) -> HTTPException:
    return HTTPException(status_code=403, detail=str(exc) or "scope denied")


@router.get("/analysis-cache")
def analysis_cache(
    target_type: str = Query(..., min_length=1),
    target_id: str = Query(..., min_length=1),
    derive_method: str = "",
    staff=Depends(require_tab("vkpi", "read")),
):
    del staff
    target_type = target_type.strip()
    target_id = target_id.strip()
    derive_method = derive_method.strip()
    if not target_type or not target_id:
        raise HTTPException(status_code=400, detail="target_type and target_id required")
    entry = get_analysis_cache_entry(target_type, target_id, derive_method=derive_method or None)
    return {
        "target_type": target_type,
        "target_id": target_id,
        "derive_method": derive_method or None,
        "state": "ready" if entry else "pending",
        "entry": entry,
    }


@router.get("/projects")
def projects(
    stage: str = "",
    staff_id: int | None = None,
    starred: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    staff=Depends(require_tab("vkpi", "read")),
):
    return workflow.list_projects(limit=limit, stage=stage, staff=staff, staff_id_filter=staff_id, starred_only=starred)


@router.get("/projects/{project_id}")
def project_detail(project_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        return workflow.project_detail(project_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/projects/{project_id}/kols")
def add_project_kols(project_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return workflow.add_project_kols(project_id, body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/projects/{project_id}/kols/{kol_ref}/advance")
def advance_project_kol(project_id: int, kol_ref: str, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return workflow.advance_project_kol_assignment(project_id, kol_ref, body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/projects/{project_id}/kols/{kol_ref}/shipping")
def update_project_kol_shipping(project_id: int, kol_ref: str, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        result = workflow.update_project_kol_shipping(project_id, kol_ref, body, staff=staff)
        cost_results = []
        shipping_amount = body.get("shipping_cost_usd", body.get("shippingFee", body.get("shipping_cost", 0)))
        product_amount = body.get("product_cost_usd", body.get("productCost", body.get("product_cost", 0)))
        if shipping_amount:
            cost_results.append(costs.add_cost({
                "project_id": project_id,
                "cost_type": "shipping",
                "amount_usd": shipping_amount,
                "source_ref": f"assignment_shipping:{result['assignment'].get('id')}",
                "note": body.get("note") or body.get("tracking_number") or body.get("no") or "",
                "metadata": {"assignment_id": result["assignment"].get("id"), "kol_pool_id": result["assignment"].get("kol_pool_id"), "carrier": body.get("carrier")},
            }, staff=staff))
        if product_amount:
            cost_results.append(costs.add_cost({
                "project_id": project_id,
                "cost_type": "product",
                "amount_usd": product_amount,
                "source_ref": f"assignment_product:{result['assignment'].get('id')}",
                "note": body.get("product_note") or "KOL shipping product cost",
                "metadata": {"assignment_id": result["assignment"].get("id"), "kol_pool_id": result["assignment"].get("kol_pool_id"), "products": body.get("products") or []},
            }, staff=staff))
        return {**result, "cost_results": cost_results}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/projects/{project_id}/kols/{kol_ref}/{action_kind}")
def project_kol_action_stub(project_id: int, kol_ref: str, action_kind: str, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    if action_kind not in {"screenshot", "video", "contract"}:
        raise HTTPException(status_code=404, detail="unknown action")
    try:
        if action_kind == "video":
            return workflow.record_project_kol_video(project_id, kol_ref, body, staff=staff)
        return workflow.project_kol_action_stub(project_id, kol_ref, body, kind=action_kind, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/projects")
def create_project(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return workflow.create_project(body, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.patch("/projects/{project_id}")
def update_project(project_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return workflow.update_project(project_id, body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.patch("/projects/{project_id}/follow-status")
def update_project_follow_status(project_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return workflow.update_project(project_id, {"follow_status": body.get("follow_status") or body.get("followStatus")}, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.patch("/projects/{project_id}/star")
def update_project_star(project_id: int, body: dict, request: Request, staff=Depends(require_tab("vkpi", "write"))):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return workflow.set_project_star(project_id, bool(body.get("starred")), staff={**staff, "user_id": int(user.get("id") or 0)})
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/projects/{project_id}/stage")
def transition_project(project_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return workflow.transition_project(project_id, body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.delete("/projects/{project_id}")
def delete_project(project_id: int, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return workflow.delete_project(project_id, body or {}, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/projects/{project_id}/ship")
def ship_project(project_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    payload = {**body, "to_stage": "shipped", "event_type": "ship"}
    try:
        return workflow.transition_project(project_id, payload, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/projects/{project_id}/publish")
def publish_project(project_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    payload = {**body, "to_stage": "published", "event_type": "publish"}
    try:
        return workflow.transition_project(project_id, payload, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/projects/{project_id}/costs")
def add_project_cost(project_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return costs.add_cost({**body, "project_id": project_id}, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/projects/{project_id}/messages")
def add_project_message(project_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return workflow.add_project_message(project_id, body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/projects/{project_id}/content")
def add_project_content(project_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return workflow.add_project_content(project_id, body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/projects/{project_id}/terms")
def upsert_project_terms(project_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return workflow.upsert_project_terms(project_id, body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/projects/{project_id}/shipments")
def add_project_shipment(project_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return workflow.add_project_shipment(project_id, body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
