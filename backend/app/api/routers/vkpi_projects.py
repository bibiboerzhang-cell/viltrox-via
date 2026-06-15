"""V-KPI project workflow routes."""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile

from app.api.dependencies.perms import require_tab
from app.core.security import get_current_user
from app.domains import costs
from app.domains.access import policy, scope
from app.domains.analysis.cache_repo import get_analysis_cache_entry, list_project_video_analysis_cache
from app.domains.projects import contract_assist
from app.domains.projects import contracts
from app.domains.projects import retrospective_aggregate
from app.domains.projects import workflow

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-projects"])
MAX_CONTRACT_UPLOAD_BYTES = 25 * 1024 * 1024


def _scope_403(exc: Exception) -> HTTPException:
    return HTTPException(status_code=403, detail=str(exc) or "scope denied")


# 波2 R2 收口(2026-06-12):原 key-regex 只盖 payment/account/bank 类结构化键,
# 漏了三条旁路——顶层 fee_amount/fee_currency、manual_overrides_json 内费用键、
# raw_extracted_json.summary 散文(会回声「总费用/分期支付」付款节奏)。
# 现把 fee 类键(fee_amount/fee_currency/total_fee…)与 summary/payment_terms 类
# 散文键一并纳入遮蔽;_safe_row 已把 *_json 列 loads 成 dict,递归可达嵌套键。
# 豁免集不变:finance/cost can_view_all 或项目成员(见 _mask_payment_fields)。
_PAYMENT_KEY_RE = re.compile(
    r"payment|account|iban|swift|bank|payee|beneficiary|routing|fee|summary",
    re.IGNORECASE,
)
_PAYMENT_MASK = "***"


def _mask_payment_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (_PAYMENT_MASK if _PAYMENT_KEY_RE.search(str(key)) and val not in (None, "", [], {}) else _mask_payment_values(val))
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [_mask_payment_values(item) for item in value]
    return value


def _mask_payment_fields(result: Any, staff: dict | None, *, project_id: int | None = None) -> Any:
    """批D 收款遮蔽(2026-06-12)+ 波2 R2 收口:合同详情/列表返回里的收款敏感字段
    (payment/account/iban/swift/bank 类 + fee_amount/fee_currency 费用键 +
    summary/payment_terms 散文键,含 raw_extracted_json/manual_overrides_json 等
    json 内嵌套键)对非 can_view_all(finance/cost 域)且非项目 assigned/creator
    的员工遮蔽为 "***"。空值保留原样,前端可区分"未填"与"被遮蔽"。
    注:_mask_payment_values 逐层重建 dict/list,原对象不被就地篡改。"""
    if scope.can_view_all(staff, domain="finance") or scope.can_view_all(staff, domain="cost"):
        return result
    if project_id is not None and scope.is_project_member(int(project_id), staff):
        return result
    return _mask_payment_values(result)


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


@router.get("/projects/deliverable-stages-summary")
def deliverable_stages_summary(staff=Depends(require_tab("vkpi", "read"))):
    """履约观测 P0(只读):assignment 阶段分布,看状态词是否分裂。

    RBAC(PV-4):own-only 员工只统计自己负责/创建的项目;管理层看全部。
    """
    from app.domains.projects import fulfillment_observation

    return fulfillment_observation.deliverable_stages_summary(staff=staff)


@router.get("/projects/due-list")
def projects_due_list(
    days_overdue: int = Query(default=7, ge=0, le=90),
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    """履约观测 刀1(只读):已签收满 N 天但项目无内容证据的待观察项。

    RBAC(PV-4):own-only 员工只看自己负责/创建的项目;管理层看全部。
    """
    from app.domains.projects import fulfillment_observation

    return fulfillment_observation.due_list(days_overdue=days_overdue, limit=limit, staff=staff)


@router.get("/projects/observation-tasks")
def list_observation_tasks(
    status: str = Query(default="pending"),
    project_id: int | None = Query(default=None),
    staff=Depends(require_tab("vkpi", "read")),
):
    """履约 stage-2(只读):列人工复核观察任务。

    RBAC(PV-4):own-only 员工只见自己负责/创建项目的任务;管理层全见。
    """
    from app.domains.projects import fulfillment_tasks

    return fulfillment_tasks.list_observation_tasks(staff=staff, status=status, project_id=project_id)


@router.post("/projects/{project_id}/observation-tasks")
def create_observation_task(
    project_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
):
    """履约 stage-2(SAFE):创建一条 pending 观察任务。零自动裁决——只建待人看任务。"""
    from app.domains.projects import fulfillment_tasks

    return fulfillment_tasks.create_observation_task(
        project_id=project_id,
        task_type=str(body.get("task_type") or ""),
        reason=body.get("reason"),
        staff=staff,
        kol_pool_id=body.get("kol_pool_id"),
    )


@router.patch("/projects/observation-tasks/{task_id}")
def mark_observation_task(
    task_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
):
    """履约 stage-2(SAFE):把任务标 reviewed/dismissed。仅触任务行,绝不改项目/派单/费用。"""
    from app.domains.projects import fulfillment_tasks

    return fulfillment_tasks.mark_observation_task(
        task_id=task_id,
        action=str(body.get("action") or ""),
        staff=staff,
        note=str(body.get("note") or ""),
    )


@router.post("/projects/observation-tasks/scan-due")
def scan_due_into_tasks(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
):
    """履约 stage-2(SAFE 手动触发):把 due-list 项 CREATE 成 content_due 复核任务。

    不自动跑、不裁决——只为人建任务。无 delivered shipment 时 created=[](物流断流,诚实)。
    """
    from app.domains.projects import fulfillment_observation

    days_overdue = body.get("days_overdue", 7)
    try:
        days_overdue = int(days_overdue)
    except (TypeError, ValueError):
        days_overdue = 7
    return fulfillment_observation.scan_due_into_tasks(staff=staff, days_overdue=days_overdue)


@router.get("/projects/observation-windows")
def list_observation_windows(
    status: str = Query(default="pending"),
    project_id: int | None = Query(default=None),
    staff=Depends(require_tab("vkpi", "read")),
):
    """履约观察窗口(只读):列物流签收后开的「等内容」窗口。

    RBAC(PV-4):own-only 员工只见自己负责/创建项目的窗口;管理层全见。
    空=无 delivered shipment 开窗(物流断流,诚实)。
    """
    from app.domains.projects import observation_windows

    return observation_windows.list_windows(staff=staff, status=status, project_id=project_id)


@router.post("/projects/observation-windows/scan-delivered")
def scan_delivered_into_windows(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
):
    """履约观察窗口(SAFE 手动触发):扫已签收派单 → CREATE 待人核观察窗口。

    不自动跑、不裁决——只为人建窗口。无 delivered shipment 时 created=[](物流断流,诚实)。
    """
    from app.domains.projects import observation_windows

    days_overdue = body.get("days_overdue", 7)
    try:
        days_overdue = int(days_overdue)
    except (TypeError, ValueError):
        days_overdue = 7
    return observation_windows.scan_delivered_into_windows(staff=staff, days_overdue=days_overdue)


@router.get("/projects/content-posts")
def list_content_posts(
    status: str = Query(default="candidate"),
    project_id: int | None = Query(default=None),
    staff=Depends(require_tab("vkpi", "read")),
):
    """履约内容帖子候选(只读):列窗口内扫到的疑似内容候选。

    RBAC(PV-4):own-only 员工只见自己负责/创建项目的候选;管理层全见。
    空=无窗口/无扫到内容(诚实)。
    """
    from app.domains.projects import observation_windows

    return observation_windows.list_content_posts(staff=staff, status=status, project_id=project_id)


@router.patch("/projects/content-posts/{post_id}")
def review_content_post(
    post_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
):
    """履约内容帖子候选(SAFE):人工复核标 matched/rejected/needs_review。

    仅触帖子行 status,绝不连带改项目/派单/费用/复盘。
    """
    from app.domains.projects import observation_windows

    return observation_windows.review_content_post(
        post_id=post_id,
        action=str(body.get("action") or ""),
        staff=staff,
        note=str(body.get("note") or ""),
    )


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
