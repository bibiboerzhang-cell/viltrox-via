"""V-KPI project workflow routes."""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile

from app.api.dependencies.perms import require_tab
from app.core.security import get_current_user
from app.domains import costs
from app.domains.access import scope
from app.domains.analysis.cache_repo import get_analysis_cache_entry, list_project_video_analysis_cache
from app.domains.projects import contracts
from app.domains.projects import retrospective_aggregate
from app.domains.projects import workflow

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-projects"])
MAX_CONTRACT_UPLOAD_BYTES = 25 * 1024 * 1024


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


@router.post("/projects/logistics-sync/enqueue")
def enqueue_logistics_sync(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """17track 物流同步入队(2026-06-12;无 token 诚实返回 blocked)。"""
    from app.domains.logistics import seventeen_track

    return seventeen_track.enqueue_logistics_sync_job(
        project_id=body.get("project_id"),
        staff=staff,
    )


@router.get("/projects/contract-templates")
def get_contract_templates(staff=Depends(require_tab("vkpi", "read"))) -> dict:
    """合同模板目录(2026-06-12 生成器 v1):槽位 schema 供前端表单渲染。"""
    del staff
    from app.domains.projects import contract_generator

    return contract_generator.list_templates()


@router.get("/projects/{project_id}")
def project_detail(project_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        return workflow.project_detail(project_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/projects/{project_id}/video-analysis-cache")
def project_video_analysis_cache(
    project_id: int,
    derive_method: str = "video_analysis_final_v1",
    staff=Depends(require_tab("vkpi", "read")),
):
    del staff
    # 向后兼容:单值返回旧形状;逗号分隔的多值返回 by_method 映射,供前端一次取回拆多份(批5)。
    methods = [m.strip() for m in str(derive_method or "").split(",") if m.strip()] or ["video_analysis_final_v1"]
    if len(methods) == 1:
        return list_project_video_analysis_cache(project_id, derive_method=methods[0])
    return {
        "project_id": int(project_id),
        "by_method": {m: list_project_video_analysis_cache(project_id, derive_method=m) for m in methods},
    }


@router.post("/projects/{project_id}/retrospective/generate")
def generate_project_retrospective(project_id: int, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return retrospective_aggregate.enqueue_project_retrospective(project_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/projects/{project_id}/retrospective")
def project_retrospective(project_id: int, staff=Depends(require_tab("vkpi", "read"))):
    del staff
    # R1:cache 只存 ready/stale,但失败必须读端可见 —— 回传最新 job 的终态 + last_error,
    # 否则复刻"主流程绿、富化静默失败"旧病。
    entry = get_analysis_cache_entry("project", str(int(project_id)), derive_method=retrospective_aggregate.DERIVE_METHOD)
    active_job = retrospective_aggregate.latest_retrospective_job(int(project_id))
    return {
        "project_id": int(project_id),
        "retrospective": entry,
        "active_job": active_job.get("active") if active_job else None,
        "last_job": active_job.get("last") if active_job else None,
    }


@router.get("/projects/{project_id}/contracts")
def project_contracts(project_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        return contracts.list_contracts(project_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/projects/{project_id}/contracts/generate")
def generate_project_contract(
    project_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """模板填槽生成合同 DOCX 并落档(确定性,LLM 零参与;正文=法务冻结模板)。"""
    from app.domains.projects import contract_generator

    try:
        return contract_generator.generate_contract(
            int(project_id),
            template_key=str(body.get("template_key") or ""),
            fields=dict(body.get("fields") or {}),
            assignment_id=body.get("assignment_id"),
            kol_pool_id=body.get("kol_pool_id"),
            staff=staff,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        if exc.__class__.__name__ == "ScopeDenied":
            raise HTTPException(status_code=403, detail=str(exc) or "scope denied") from exc
        raise


@router.post("/projects/{project_id}/contracts/upload")
async def upload_project_contract(
    project_id: int,
    file: UploadFile = File(...),
    assignment_id: int | None = Form(default=None),
    kol_pool_id: int | None = Form(default=None),
    staff=Depends(require_tab("vkpi", "write")),
):
    filename = file.filename or "contract.pdf"
    suffix = Path(filename).suffix.lower() or ".pdf"
    try:
        with tempfile.TemporaryDirectory(prefix="vkpi-contract-upload-") as tmpdir:
            local_path = Path(tmpdir) / f"upload{suffix}"
            size = 0
            with local_path.open("wb") as out:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_CONTRACT_UPLOAD_BYTES:
                        raise HTTPException(status_code=413, detail="contract file too large")
                    out.write(chunk)
            return contracts.create_contract_from_file(
                project_id,
                str(local_path),
                file_name=filename,
                mime_type=file.content_type or "",
                assignment_id=assignment_id,
                kol_pool_id=kol_pool_id,
                staff=staff,
            )
    except HTTPException:
        raise
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/projects/{project_id}/contracts/{contract_id}/download")
def project_contract_download(project_id: int, contract_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        return contracts.contract_download_url(project_id, contract_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.patch("/projects/{project_id}/contracts/{contract_id}")
def update_project_contract(project_id: int, contract_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return contracts.update_contract(project_id, contract_id, body or {}, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/projects/{project_id}/contracts/{contract_id}/confirm")
def confirm_project_contract(project_id: int, contract_id: int, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return contracts.confirm_contract(project_id, contract_id, body or {}, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.delete("/projects/{project_id}/contracts/{contract_id}")
def delete_project_contract(project_id: int, contract_id: int, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return contracts.delete_contract(project_id, contract_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/projects/{project_id}/contracts/{contract_id}/extract")
def extract_project_contract(project_id: int, contract_id: int, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return contracts.enqueue_contract_extract_job(project_id, contract_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
