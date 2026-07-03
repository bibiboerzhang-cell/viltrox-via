"""V-KPI project workflow routes."""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile

from app.api.dependencies.perms import require_tab
from app.core.security import get_current_user
from app.domains import costs
from app.domains.access import policy, scope
from app.domains.analysis.cache_repo import get_analysis_cache_entry, list_project_video_analysis_cache
from app.domains.projects import automation_audit
from app.domains.projects import contract_assist
from app.domains.projects import contracts
from app.domains.projects import retrospective_aggregate
from app.domains.projects import workflow

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-projects"])

# 行为不变搬迁:收款遮蔽簇 + _scope_403 + MAX_CONTRACT_UPLOAD_BYTES 已整体 move 到
# vkpi_projects_masking.py;此处显式 re-export(含下划线私有名)兜住所有调用点。
from app.api.routers.vkpi_projects_masking import (  # noqa: E402
    MAX_CONTRACT_UPLOAD_BYTES,
    _mask_payment_fields,
    _mask_payment_values,
    _PAYMENT_KEY_RE,
    _PAYMENT_MASK,
    _scope_403,
)

# 行为不变搬迁:履约/观察窗口/内容帖候选/履约sweep/发货审批/advance-retrospective 内聚簇
# 整组 move 到 vkpi_projects_fulfillment.py(子 router 无 prefix);此处 include_router 兜住,
# 路由顺序保留(子 router 先于本文件 /projects/{project_id} 注册)。
from app.api.routers import vkpi_projects_fulfillment as _fulfillment_sub  # noqa: E402

router.include_router(_fulfillment_sub.router)


def _resolve_video_cached_url(evidence_id: str) -> str | None:
    """按 video 证据 id 解析其 R2 缓存视频地址,供前端内联播放器与分镜分析共用一条轮询。

    背景:URL 结果卡从「会话历史」重建时会丢掉实时算出的 cached_video_url(历史里没存),
    导致分镜出来了、播放器不出。这里在分镜分析缓存接口顺带解析:证据 -> 平台/原生短码
    -> 现成的 cached_video_url_for_item(键与 worker 一致)。纯只读,任何异常静默返回 None,
    绝不影响分析主体渲染,绝不触碰 viltrox_fit_score。
    """
    try:
        eid = int(str(evidence_id).strip())
    except (TypeError, ValueError):
        return None
    if eid <= 0:
        return None
    try:
        from app.db.connection import get_conn
        from app.domains.kol.url_deep_crawl import classify_url
        from app.domains.media.cache import cached_video_url_for_item

        conn = get_conn()
        row = conn.execute(
            "SELECT platform, content_url FROM vkpi_kol_video_evidence WHERE id = ?",
            (eid,),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        platform = str(data.get("platform") or "").strip().lower()
        content_url = str(data.get("content_url") or "").strip()
        if not platform or not content_url:
            return None
        classified = classify_url(content_url)
        video_key = str(getattr(classified, "video_id", "") or "").strip()
        if not video_key:
            return None
        return cached_video_url_for_item(platform, video_key)
    except Exception:
        return None


@router.get("/analysis-cache")
def analysis_cache(
    target_type: str = Query(..., min_length=1),
    target_id: str = Query(..., min_length=1),
    derive_method: str = "",
    staff=Depends(require_tab("vkpi", "read")),
):
    target_type = target_type.strip()
    target_id = target_id.strip()
    derive_method = derive_method.strip()
    if not target_type or not target_id:
        raise HTTPException(status_code=400, detail="target_type and target_id required")
    # 批D 权限收口(2026-06-12):原 del staff 绕过项目级 scope。能映射回项目的目标
    # (project/contract/video)走 assert_project_access;kol_pool 等无项目维度目标
    # 维持 tab 级权限(require_tab 已生效)。
    scoped_project_id = scope.resolve_analysis_target_project(target_type, target_id)
    if scoped_project_id is not None:
        try:
            policy.require_project_read(scoped_project_id, staff)
        except policy.ScopeDenied as exc:
            raise _scope_403(exc) from exc
    entry = get_analysis_cache_entry(target_type, target_id, derive_method=derive_method or None)
    result = {
        "target_type": target_type,
        "target_id": target_id,
        "derive_method": derive_method or None,
        "state": "ready" if entry else "pending",
        "entry": entry,
    }
    # 视频目标:顺带解析 R2 缓存视频地址,供前端内联播放器(与分镜分析共用同一轮询,
    # 历史重建/实时执行都稳)。只读,缺失则不附带该字段。
    if target_type.lower() == "video":
        cached_video_url = _resolve_video_cached_url(target_id)
        if cached_video_url:
            result["cached_video_url"] = cached_video_url
    if target_type.lower() == "contract":
        result = _mask_payment_fields(result, staff, project_id=scoped_project_id)
    return result


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


@router.get("/projects/invoice-extract/{extract_key}")
def project_invoice_extract(extract_key: str, staff=Depends(require_tab("vkpi", "read"))):
    """发票提取读端(批E)。静态段 invoice-extract 须定义在 /projects/{project_id} 之前。
    产物含收款敏感字段:能映射回项目时按项目级 scope 把关。"""
    try:
        entry = contract_assist.get_invoice_extract(extract_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result_project_id = ((entry.get("result") or {}) if isinstance(entry.get("result"), dict) else {}).get("project_id")
    if result_project_id:
        try:
            policy.require_project_read(int(result_project_id), staff)
        except policy.ScopeDenied as exc:
            raise _scope_403(exc) from exc
    return entry


@router.get("/projects/contract-polish/{polish_key}")
def project_contract_polish(polish_key: str, staff=Depends(require_tab("vkpi", "read"))):
    """合同润色读端(批E)。静态段 contract-polish 须定义在 /projects/{project_id} 之前。"""
    try:
        entry = contract_assist.get_contract_polish(polish_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result_project_id = ((entry.get("result") or {}) if isinstance(entry.get("result"), dict) else {}).get("project_id")
    if result_project_id:
        try:
            policy.require_project_read(int(result_project_id), staff)
        except policy.ScopeDenied as exc:
            raise _scope_403(exc) from exc
    return entry


@router.post("/projects/{project_id}/invoice-extract/enqueue")
def enqueue_project_invoice_extract(
    project_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
):
    """发票回填提取入队(批E):文件已由 /evidence/uploads 落盘,这里只收 file_url。"""
    try:
        return contract_assist.enqueue_invoice_extract_job(
            project_id,
            str(body.get("file_url") or ""),
            file_name=str(body.get("file_name") or ""),
            staff=staff,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/projects/{project_id}/contract-polish/enqueue")
def enqueue_project_contract_polish(
    project_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
):
    """合同条款 LLM 润色入队(批E):只收文本类槽,LLM 经 apify_jobs 队列。"""
    try:
        return contract_assist.enqueue_contract_polish_job(
            project_id,
            template_key=str(body.get("template_key") or ""),
            fields=dict(body.get("fields") or {}),
            staff=staff,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/projects/{project_id}/members")
def list_project_members(project_id: int, staff=Depends(require_tab("vkpi", "read"))):
    """真·项目共享成员列表(2026-06-14)。读端按项目级 scope 把关:能看见该项目者
    (own / 共享成员 / admin)才能列其成员名单。"""
    from app.domains.projects import project_members

    try:
        policy.require_project_read(int(project_id), staff)
    except policy.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    return project_members.list_members(int(project_id))


@router.post("/projects/{project_id}/members")
def add_project_member(project_id: int, body: dict = Body(default_factory=dict), staff=Depends(require_tab("vkpi", "write"))):
    """把项目共享给某员工(只有项目 owner/creator 或 can_view_all 可加)。
    body: {staff_id, role}('viewer' 只读 / 'editor' 可写)。"""
    from app.domains.projects import project_members

    try:
        project_members.assert_can_manage_members(int(project_id), staff)
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    target_staff_id = body.get("staff_id")
    if target_staff_id in (None, "", 0, "0"):
        raise HTTPException(status_code=400, detail="staff_id required")
    result = project_members.add_member(
        int(project_id),
        int(target_staff_id),
        role=str(body.get("role") or "viewer"),
        added_by_staff_id=scope.actor_staff_id(staff) or None,
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("error") or "add member failed")
    return result


@router.delete("/projects/{project_id}/members/{staff_id}")
def remove_project_member(project_id: int, staff_id: int, staff=Depends(require_tab("vkpi", "write"))):
    """撤销共享(只有项目 owner/creator 或 can_view_all 可删)。"""
    from app.domains.projects import project_members

    try:
        project_members.assert_can_manage_members(int(project_id), staff)
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    result = project_members.remove_member(int(project_id), int(staff_id), removed_by_staff_id=scope.actor_staff_id(staff) or None)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("error") or "remove member failed")
    return result


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
    # 批D 权限收口(2026-06-12):原 del staff 绕过项目级 scope,改为读模式断言。
    try:
        policy.require_project_read(int(project_id), staff)
    except policy.ScopeDenied as exc:
        raise _scope_403(exc) from exc
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
    # 批D 权限收口(2026-06-12):原 del staff 绕过项目级 scope,改为读模式断言。
    try:
        policy.require_project_read(int(project_id), staff)
    except policy.ScopeDenied as exc:
        raise _scope_403(exc) from exc
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


@router.get("/projects/{project_id}/timeline")
def project_timeline(project_id: int, staff=Depends(require_tab("vkpi", "read"))):
    """W2 只读:项目履约时间线(建→选→寄→签→观察→发布→复盘),按 canonical 阶段有序。

    纯聚合既有履约/项目表;零业务写、不碰 viltrox_fit_score。项目级 scope 收口(读模式)。
    """
    try:
        policy.require_project_read(int(project_id), staff)
    except policy.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    return automation_audit.build_project_timeline(int(project_id))


@router.get("/projects/{project_id}/automation-audit")
def project_automation_audit(
    project_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    """W2 只读:项目自动化审计行(哪单同步/哪天开窗/扫了谁/命中啥/为何进复盘)。"""
    try:
        policy.require_project_read(int(project_id), staff)
    except policy.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    return {
        "project_id": int(project_id),
        "items": automation_audit.list_project_audit(int(project_id), limit=limit),
    }


@router.get("/projects/{project_id}/contracts")
def project_contracts(project_id: int, staff=Depends(require_tab("vkpi", "read"))):
    # 项目级 scope 由 contracts.list_contracts 内部 assert_project_access 把关(读模式)。
    try:
        return _mask_payment_fields(contracts.list_contracts(project_id, staff=staff), staff, project_id=project_id)
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
            output_format=str(body.get("format") or "pdf"),
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
    related_contract_id: int | None = Form(default=None),
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
                related_contract_id=related_contract_id,
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
        return _mask_payment_fields(contracts.update_contract(project_id, contract_id, body or {}, staff=staff), staff, project_id=project_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/projects/{project_id}/contracts/{contract_id}/confirm")
def confirm_project_contract(project_id: int, contract_id: int, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return _mask_payment_fields(contracts.confirm_contract(project_id, contract_id, body or {}, staff=staff), staff, project_id=project_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    # 残账收窄(批E,2026-06-12):兜底白名单只留 screenshot/video;
    # contract 早已走 /projects/{project_id}/contracts/upload 真归档(前端不再调旧路径),显式 410。
    if action_kind == "contract":
        raise HTTPException(
            status_code=410,
            detail="contract action retired: use POST /api/admin/vkpi/projects/{project_id}/contracts/upload",
        )
    if action_kind not in {"screenshot", "video"}:
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
    except ValueError as exc:  # 含 ShipmentNotApproved:发货审批未通过 → 409
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _material_row_to_item(row: dict) -> dict:
    """物料行出参整理:metadata_json 解成 dict,坏 JSON 容错为空对象,不让单行坏数据炸整表。"""
    import json as _json

    item = dict(row)
    try:
        meta = _json.loads(str(item.pop("metadata_json", None) or "{}"))
    except Exception:
        meta = {}
    item["metadata"] = meta if isinstance(meta, dict) else {}
    return item


@router.get("/projects/{project_id}/materials")
def list_project_materials(project_id: int, staff=Depends(require_tab("vkpi", "read"))):
    """项目物料库列表(P1,2026-07-03):复用 vkpi_content_assets 表——asset_type='material'
    且 post_id 为空的行即项目物料(零新表;文件本体走既有 evidence uploads 存储)。
    读端按项目级 scope 把关,与 timeline/retrospective 同口径。"""
    try:
        policy.require_project_read(int(project_id), staff)
    except policy.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    # 懒 import:线上旧布局下函数内引用零风险(部署陷阱口诀)。
    from app.db.connection import get_conn
    from app.platform.db.schema import ensure_vkpi_schema

    ensure_vkpi_schema()
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, project_id, asset_url, asset_type, usage_rights, metadata_json, created_at
        FROM vkpi_content_assets
        WHERE project_id = ? AND asset_type = 'material' AND post_id IS NULL
        ORDER BY id DESC
        LIMIT 200
        """,
        (int(project_id),),
    ).fetchall()
    items = [_material_row_to_item(dict(row)) for row in rows]
    return {"project_id": int(project_id), "items": items, "count": len(items)}


@router.post("/projects/{project_id}/materials")
def add_project_material(project_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    """登记一条项目物料(P1):文件先经 POST /evidence/uploads 落盘拿 file_url,这里只收
    地址与元信息入库归档。复用 vkpi_content_assets:post_id 置空 + asset_type='material'
    区分,不建新表、不动 content post 相关聚合(其均按 post_id join,空值互不干扰)。"""
    payload = body or {}
    asset_url = str(payload.get("asset_url") or payload.get("file_url") or "").strip()
    if not asset_url:
        raise HTTPException(status_code=400, detail="asset_url required")
    try:
        scope.assert_project_access(int(project_id), staff, write=True)
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    import json as _json
    from datetime import datetime as _dt, timezone as _tz

    from app.db.connection import get_conn
    from app.platform.db.schema import ensure_vkpi_schema

    ensure_vkpi_schema()
    conn = get_conn()
    if not conn.execute("SELECT 1 FROM vkpi_projects WHERE id=?", (int(project_id),)).fetchone():
        raise HTTPException(status_code=404, detail="project not found")
    try:
        size = int(payload.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    meta = {
        "file_name": str(payload.get("file_name") or "")[:200],
        "file_type": str(payload.get("file_type") or "")[:100],
        "size": size,
        "note": str(payload.get("note") or "")[:500],
        "uploaded_by_staff_id": scope.actor_staff_id(staff) or None,
    }
    now = _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        """
        INSERT INTO vkpi_content_assets (
            post_id, project_id, asset_url, asset_type, usage_rights, metadata_json, created_at
        ) VALUES (?,?,?,?,?,?,?)
        """,
        (None, int(project_id), asset_url, "material", "internal", _json.dumps(meta, ensure_ascii=False), now),
    )
    conn.commit()
    row = conn.execute(
        """
        SELECT id, project_id, asset_url, asset_type, usage_rights, metadata_json, created_at
        FROM vkpi_content_assets
        WHERE project_id = ? AND asset_type = 'material' AND post_id IS NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        (int(project_id),),
    ).fetchone()
    item = _material_row_to_item(dict(row)) if row else {}
    # 审计留痕:best-effort,记账失败不影响物料入库主流程。
    try:
        from app.domains import audit

        audit.log_business_event(
            staff_id=scope.actor_staff_id(staff),
            action_type="project_material_add",
            target_type="project",
            target_id=int(project_id),
            detail=asset_url,
            metadata={"asset_id": item.get("id"), "file_name": meta["file_name"]},
        )
    except Exception:
        pass
    return {"status": "stored", "item": item}


@router.post("/kol-pool/{kol_pool_id}/enqueue-profile-crawl")
def enqueue_kol_profile_crawl(kol_pool_id: int, staff=Depends(require_tab("vkpi", "write"))) -> dict:
    """新发现 KOL 档案瘦(关注/帖数/均播空)→ 抽屉打开时自动补:入队 kol_profile_deep_crawl
    (worker 级联:抓帖建 evidence → final_v1 followup 补档案/评论 → 受众自动链)。
    幂等:已有 evidence 或活跃同类 job 直接跳过,不重复烧 Apify。红线:零触 fit。"""
    del staff
    import json as _json
    from datetime import datetime as _dt, timezone as _tz

    from app.db.connection import get_conn as _gc

    conn = _gc()
    row = conn.execute(
        "SELECT id, handle, profile_url FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)
    ).fetchone()
    if not row:
        return {"status": "not_found"}
    rec = dict(row)
    url = str(rec.get("profile_url") or "")
    if not url.startswith("http"):
        return {"status": "no_profile_url"}
    if conn.execute(
        "SELECT 1 FROM vkpi_kol_video_evidence WHERE kol_pool_id=? LIMIT 1", (int(kol_pool_id),)
    ).fetchone():
        return {"status": "already_has_evidence"}
    dup = conn.execute(
        "SELECT id FROM apify_jobs WHERE job_type=? "
        "AND status IN ('queued','running','retrying','processing') AND CAST(payload AS TEXT) LIKE ? LIMIT 1",
        ("kol_profile_deep_crawl", "%" + '"kol_pool_id": ' + str(int(kol_pool_id)) + ",%"),
    ).fetchone()
    if dup:
        return {"status": "already_queued", "job_id": int(dict(dup)["id"])}
    now = _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "url": url,
        "staff_id": None,
        "max_posts": 12,
        "target_id": int(kol_pool_id),
        "query_text": f"抽屉自动补档案 · {rec.get('handle')}",
        "kol_pool_id": int(kol_pool_id),
        "target_type": "kol_profile",
        "source": "drawer_auto_enrich",
    }
    conn.execute(
        "INSERT INTO apify_jobs (job_type, payload, status, created_at, updated_at) VALUES (?, ?::jsonb, ?, ?, ?)",
        ("kol_profile_deep_crawl", _json.dumps(payload, ensure_ascii=False), "queued", now, now),
    )
    conn.commit()
    return {"status": "queued"}
