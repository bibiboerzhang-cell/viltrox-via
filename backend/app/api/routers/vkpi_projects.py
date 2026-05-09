"""V-KPI project workflow routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.services.vkpi import costs, scope, workflow

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-projects"])


def _scope_403(exc: Exception) -> HTTPException:
    return HTTPException(status_code=403, detail=str(exc) or "scope denied")
@router.get("/projects")
def projects(
    stage: str = "",
    staff_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    staff=Depends(require_tab("vkpi", "read")),
):
    return workflow.list_projects(limit=limit, stage=stage, staff=staff, staff_id_filter=staff_id)


@router.get("/projects/{project_id}")
def project_detail(project_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        return workflow.project_detail(project_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
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

