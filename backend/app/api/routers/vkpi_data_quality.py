"""V-KPI data quality routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.domains import data_quality as data_quality_domain
from app.services.vkpi import data_quality

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-data-quality"])


def _is_manager_staff(staff: dict) -> bool:
    role = str(staff.get("role") or "").strip().lower()
    if int(staff.get("is_owner") or 0) == 1:
        return True
    return role in {"admin", "manager", "lead", "marketing_lead", "marketing_manager", "marketing-manager"}


def _require_manager_staff(staff: dict) -> None:
    if not _is_manager_staff(staff):
        raise HTTPException(status_code=403, detail="management permission required")


@router.get("/data-quality")
def data_quality_issues(limit: int = Query(default=200, ge=1, le=1000), staff=Depends(require_tab("vkpi", "read"))):
    return data_quality_domain.list_quality_issues(limit=limit, staff=staff)


@router.post("/data-quality/{issue_id}/resolve")
def data_quality_resolve(issue_id: str, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    _require_manager_staff(staff)
    try:
        return data_quality.act_on_issue(issue_id, action="resolve", body=body or {}, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/data-quality/{issue_id}/ignore")
def data_quality_ignore(issue_id: str, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    _require_manager_staff(staff)
    try:
        return data_quality.act_on_issue(issue_id, action="ignore", body=body or {}, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/data-quality/{issue_id}/assign")
def data_quality_assign(issue_id: str, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    _require_manager_staff(staff)
    try:
        return data_quality.act_on_issue(issue_id, action="assign", body=body or {}, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/data-quality/{issue_id}/rerun")
def data_quality_rerun(issue_id: str, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    _require_manager_staff(staff)
    try:
        return data_quality.act_on_issue(issue_id, action="rerun", body=body or {}, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/data-quality/{issue_id}/evidence")
def data_quality_evidence(issue_id: str, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    _require_manager_staff(staff)
    try:
        return data_quality.act_on_issue(issue_id, action="evidence", body=body or {}, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/data-quality/{issue_id}/reopen")
def data_quality_reopen(issue_id: str, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    _require_manager_staff(staff)
    try:
        return data_quality.act_on_issue(issue_id, action="reopen", body=body or {}, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
