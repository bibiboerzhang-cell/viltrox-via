"""V-KPI Events 路由 — 活动 CRUD + tasks/expenses/members/kols(多人协作)。

前缀 /api/admin/vkpi/events;读 require_tab("vkpi","read")、写 require_tab("vkpi","write")。
员工 scope 在 service 层(list_events 已按 owner/team_ids/共享成员 过滤);写操作再经
scope.assert_event_access(..., write=True)收口 —— 不再是任何 vkpi:write 都能改任意活动,
只有 owner / team 成员 / editor 共享成员 / admin(can_view_all)可写(2026-06-14,镜像项目共享)。
与 KOL Pool / 评分域物理隔离;迁移 122 的 4 表 + 迁移 132 的共享成员表。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger
from app.domains.access import policy, scope
from app.domains.events import event_members, service


logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi/events", tags=["vkpi-events"])


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except HTTPException:
        raise
    except LookupError as exc:
        # 子资源写到不存在的活动等 → 404(而非直撞 FK 违约的 500)。消息是我方常量
        # ("event not found"),不透传 DB 细节。
        raise HTTPException(status_code=404, detail=str(exc) or "not found") from exc
    except ValueError as exc:
        # 入参校验失败(坏日期/数值越界等)→ 400。消息是我方 ValueError 文案,不含 PG DETAIL。
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        # Includes the safe code/schema rollout gap: a non-default workspace
        # must never fall back into legacy org-1 Event rows before migration 244.
        raise HTTPException(status_code=403, detail=str(exc) or "scope denied") from exc
    except Exception as exc:  # noqa: BLE001 — DB/序列化等意外 → 500
        # 绝不把 exc 原文(可能含整行数据 / PG DETAIL)透传给客户端;真因写服务端日志。
        logger.error("events endpoint error", exc_info=True)
        raise HTTPException(status_code=500, detail="internal events error") from exc


def _scope_403(exc: Exception) -> HTTPException:
    return HTTPException(status_code=403, detail=str(exc) or "scope denied")


def _assert_write(event_id: str, staff) -> None:
    """写收口:owner / team 成员 / editor 共享成员 / admin 才可改本活动,否则 403。

    T2(2026-06-14):统一走 policy.require_event_write(内部仍是 scope.assert_event_access
    write=True,零策略变更),与项目侧 policy.require_project_* 口径一致。"""
    try:
        policy.require_event_write(str(event_id), staff)
    except policy.ScopeDenied as exc:
        raise _scope_403(exc) from exc


def _assert_read(event_id: str, staff) -> None:
    """读收口(P0):直连 GET /{event_id} 此前裸奔,任意 vkpi:read 员工可拉任意活动
    的预算/团队/费用/邀约。这里按活动级 scope 把关 —— owner / team / 共享成员 /
    is_public 读 / admin 才能读,否则 403。镜像 share-members 读端 + 项目详情口径。

    T2(2026-06-14):统一走 policy.require_event_read(内部仍是 scope.assert_event_access
    read,零策略变更)。"""
    try:
        policy.require_event_read(str(event_id), staff)
    except policy.ScopeDenied as exc:
        raise _scope_403(exc) from exc


def _assert_manage_team(event_id: str, staff) -> None:
    """team_ids 成员名单管理是「转授」动作,只有活动 owner / can_view_all 可为之
    —— 比内容编辑高一级(镜像 share-members 的 owner-only 闸)。共享 editor / team 成员
    能写活动内容,但不能再把别人加进 team 拿完整读写,否则 = 提权绕过 share-members。"""
    try:
        event_members.assert_can_manage_members(str(event_id), staff)
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


# ── Events ────────────────────────────────────────────────────────────────
@router.get("")
def list_events(limit: int = Query(default=200, ge=1, le=500), staff=Depends(require_tab("vkpi", "read"))):
    return _guard(service.list_events, staff, limit=limit)


# /upcoming 必须在动态 /{event_id} 路由之前注册,否则 "upcoming" 会被当成 event_id。
@router.get("/upcoming")
def upcoming_events(limit: int = Query(default=50, ge=1, le=200), staff=Depends(require_tab("vkpi", "read"))):
    """upcoming/进行中活动(end_date>=今天)+ location + budget;给 dashboard 地图 / 报告活动进度。"""
    return _guard(service.list_upcoming_events, staff, limit=limit)


# ── Share members(真·活动共享:必须在动态 /{event_id} 路由之前注册)─────────────
@router.get("/{event_id}/share-members")
def list_event_share_members(event_id: str, staff=Depends(require_tab("vkpi", "read"))):
    """真·活动共享成员列表。读端按活动级 scope 把关:能看见该活动者
    (owner / team / 共享成员 / admin)才能列其成员名单。"""
    try:
        policy.require_event_read(str(event_id), staff)
    except policy.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    return _guard(event_members.list_members, str(event_id), staff)


@router.post("/{event_id}/share-members")
def add_event_share_member(event_id: str, body: dict = Body(default_factory=dict), staff=Depends(require_tab("vkpi", "write"))):
    """把活动共享给某员工(只有活动 owner 或 can_view_all 可加)。
    body: {staff_id, role}('viewer' 只读 / 'editor' 可写)。"""
    try:
        event_members.assert_can_manage_members(str(event_id), staff)
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    target_staff_id = body.get("staff_id")
    if target_staff_id in (None, "", 0, "0"):
        raise HTTPException(status_code=400, detail="staff_id required")
    try:
        if isinstance(target_staff_id, bool):
            raise ValueError("boolean is not a staff id")
        parsed_target_staff_id = int(target_staff_id)
    except (TypeError, ValueError, OverflowError) as exc:
        raise HTTPException(status_code=400, detail="staff_id must be a positive integer") from exc
    if parsed_target_staff_id <= 0:
        raise HTTPException(status_code=400, detail="staff_id must be a positive integer")
    result = event_members.add_member(
        str(event_id),
        parsed_target_staff_id,
        role=str(body.get("role") or "viewer"),
        added_by_staff_id=scope.actor_staff_id(staff) or None,
        staff=staff,
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("error") or "add member failed")
    return result


@router.delete("/{event_id}/share-members/{staff_id}")
def remove_event_share_member(event_id: str, staff_id: int, staff=Depends(require_tab("vkpi", "write"))):
    """撤销共享(只有活动 owner 或 can_view_all 可删)。"""
    try:
        event_members.assert_can_manage_members(str(event_id), staff)
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    result = event_members.remove_member(
        str(event_id),
        int(staff_id),
        removed_by_staff_id=scope.actor_staff_id(staff) or None,
        staff=staff,
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("error") or "remove member failed")
    return result


@router.get("/{event_id}")
def get_event(event_id: str, staff=Depends(require_tab("vkpi", "read"))):
    _assert_read(event_id, staff)
    return _guard(service.get_event_detail, event_id, staff)


@router.get("/{event_id}/retrospective")
def event_retrospective(event_id: str, staff=Depends(require_tab("vkpi", "read"))):
    """路线4 · 活动复盘聚合(预算/任务/邀约/结果 + 待补数据,只读)。"""
    _assert_read(event_id, staff)
    from app.domains.events import retrospective

    return _guard(retrospective.aggregate_event_retrospective, event_id, staff)


@router.get("/{event_id}/evidence")
def event_evidence_list(event_id: str, staff=Depends(require_tab("vkpi", "read"))):
    """F3 · 活动证据清单(发票/图片/合同/现场照)+ 发票金额合计(只读)。"""
    _assert_read(event_id, staff)
    from app.domains.events import evidence_upload

    return _guard(evidence_upload.list_evidence, event_id, staff)


@router.post("/{event_id}/evidence")
def event_evidence_add(event_id: str, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    """F3 · 记录活动证据元数据(二进制走 R2/本地;DB 仅存元数据 + storage_ref)。"""
    _assert_write(event_id, staff)  # 写证据(发票金额进汇总)必须写权限,不能只读放行
    from app.domains.events import evidence_upload

    b = body or {}
    return evidence_upload.record_evidence_meta(
        event_id, str(b.get("kind") or "other"), str(b.get("filename") or ""), str(b.get("storage_ref") or ""),
        content_type=str(b.get("content_type") or ""), size_bytes=int(b.get("size_bytes") or 0),
        amount_cents=b.get("amount_cents"), note=str(b.get("note") or ""), staff=staff,
    )


@router.post("/{event_id}/geocode")
def event_geocode(event_id: str, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    """F2 · 活动地址地理编码(免 key,OSM Nominatim)→ 写回经纬度;失败提示手动填。"""
    _assert_write(event_id, staff)  # 写回经纬度是写操作,不能只读放行
    from app.domains.events import geocode

    return _guard(geocode.geocode_and_update_event, event_id, str((body or {}).get("address") or ""), staff)


@router.post("/{event_id}/retrospective/finalize")
def event_retrospective_finalize(event_id: str, staff=Depends(require_tab("vkpi", "write"))):
    """F4 · 定格复盘:把当前复盘聚合落库一行快照(vkpi_event_retrospectives)。"""
    _assert_write(event_id, staff)  # 落库快照是写操作,不能只读放行
    from app.domains.events import retrospective

    return _guard(retrospective.finalize_event_retrospective, event_id, staff)


@router.get("/{event_id}/retrospective/latest")
def event_retrospective_latest(event_id: str, staff=Depends(require_tab("vkpi", "read"))):
    """F4 · 读最近一次定格复盘快照(无则回退实时聚合)。"""
    _assert_read(event_id, staff)
    from app.domains.events import retrospective

    return _guard(retrospective.get_latest_retrospective, event_id, staff)


@router.post("")
def create_event(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    return _guard(service.create_event, body or {}, staff)


@router.patch("/{event_id}")
def update_event(event_id: str, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    _assert_write(event_id, staff)
    # 提权闸:team_ids 改的是「谁在 team(=完整读写)」,是 owner-only 转授动作。若允许
    # 共享 editor 经通用 PATCH 写 team_ids,就能把自己/任意 staff 加进 team 拿完整读写,
    # 绕过 share-members 的 owner-only 闸。故 payload 含 team_ids 时额外要求 owner/admin。
    if "team_ids" in (body or {}):
        _assert_manage_team(event_id, staff)
    return _guard(service.update_event, event_id, body or {}, staff)


@router.delete("/{event_id}")
def delete_event(event_id: str, staff=Depends(require_tab("vkpi", "write"))):
    # Deleting the parent cascades into tasks, expenses, invites and shares; it
    # is an ownership/manager action, not ordinary team/editor content editing.
    _assert_manage_team(event_id, staff)
    return _guard(service.delete_event, event_id, staff)


# ── Members(多人协作:team_ids)─────────────────────────────────────────────
# team_ids 增删 = 成员名单管理(owner-only 转授),与通用内容编辑分级 —— 见 _assert_manage_team。
@router.post("/{event_id}/members")
def add_member(event_id: str, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    _assert_manage_team(event_id, staff)
    return _guard(service.add_member, event_id, (body or {}).get("user_id"), staff)


@router.delete("/{event_id}/members/{user_id}")
def remove_member(event_id: str, user_id: str, staff=Depends(require_tab("vkpi", "write"))):
    _assert_manage_team(event_id, staff)
    return _guard(service.remove_member, event_id, user_id, staff)


# ── Tasks ─────────────────────────────────────────────────────────────────
@router.post("/{event_id}/tasks")
def add_task(event_id: str, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    _assert_write(event_id, staff)
    return _guard(service.add_task, event_id, body or {}, staff)


@router.patch("/{event_id}/tasks/{task_id}")
def update_task(event_id: str, task_id: str, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    _assert_write(event_id, staff)
    return _guard(service.update_task, event_id, task_id, body or {}, staff)


@router.delete("/{event_id}/tasks/{task_id}")
def delete_task(event_id: str, task_id: str, staff=Depends(require_tab("vkpi", "write"))):
    _assert_write(event_id, staff)
    return _guard(service.delete_task, event_id, task_id, staff)


# ── Expenses ──────────────────────────────────────────────────────────────
@router.post("/{event_id}/expenses")
def add_expense(event_id: str, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    _assert_write(event_id, staff)
    return _guard(service.add_expense, event_id, body or {}, staff)


@router.delete("/{event_id}/expenses/{expense_id}")
def delete_expense(event_id: str, expense_id: str, staff=Depends(require_tab("vkpi", "write"))):
    _assert_write(event_id, staff)
    return _guard(service.delete_expense, event_id, expense_id, staff)


@router.post("/{event_id}/invoice-extract/enqueue")
def enqueue_event_invoice_extract(event_id: str, body: dict = Body(default_factory=dict), staff=Depends(require_tab("vkpi", "write"))):
    """报销发票 AI 识别入队(E2,2026-07-03):复用项目侧 contract_assist 发票提取管线。

    文件已由 /evidence/uploads 落盘,这里只收 file_url;LLM 经 apify_jobs 队列,产物
    落 vkpi_analysis_cache,前端轮询 GET /projects/invoice-extract/{extract_key} 读回填。
    """
    _assert_write(event_id, staff)
    # 部署陷阱:函数内懒 import + try/except —— 线上旧布局的 contract_assist 可能还没有
    # enqueue_event_invoice_extract_job,顶层 import/getattr 失败不能炸整站,这里如实 503。
    try:
        from app.domains.projects import contract_assist

        enqueue_fn = contract_assist.enqueue_event_invoice_extract_job
    except (ImportError, AttributeError) as exc:
        raise HTTPException(status_code=503, detail=f"invoice extract unavailable: {exc}") from exc
    try:
        return enqueue_fn(
            str(event_id),
            str((body or {}).get("file_url") or ""),
            file_name=str((body or {}).get("file_name") or ""),
            staff=staff,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        # budget_guard_blocked 等前置失败按 400 如实返回(与项目侧口径一致)。
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


# ── KOL invites ───────────────────────────────────────────────────────────
@router.post("/{event_id}/kols")
def invite_kol(event_id: str, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    _assert_write(event_id, staff)
    return _guard(service.invite_kol, event_id, body or {}, staff)


@router.delete("/{event_id}/kols/{invite_id}")
def remove_kol(event_id: str, invite_id: str, staff=Depends(require_tab("vkpi", "write"))):
    _assert_write(event_id, staff)
    return _guard(service.remove_kol, event_id, invite_id, staff)


# ── Materials(活动营销物料,逐项落库)──────────────────────────────────────────
@router.post("/{event_id}/materials")
def add_material(event_id: str, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    _assert_write(event_id, staff)
    return _guard(service.add_material, event_id, body or {}, staff)


@router.patch("/{event_id}/materials/{material_id}")
def update_material(event_id: str, material_id: str, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    _assert_write(event_id, staff)
    return _guard(service.update_material, event_id, material_id, body or {}, staff)


@router.delete("/{event_id}/materials/{material_id}")
def delete_material(event_id: str, material_id: str, staff=Depends(require_tab("vkpi", "write"))):
    _assert_write(event_id, staff)
    return _guard(service.delete_material, event_id, material_id, staff)


# ── Products(活动产品准备,逐项落库)──────────────────────────────────────────
@router.post("/{event_id}/products")
def add_product(event_id: str, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    _assert_write(event_id, staff)
    return _guard(service.add_product, event_id, body or {}, staff)


@router.patch("/{event_id}/products/{product_id}")
def update_product(event_id: str, product_id: str, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    _assert_write(event_id, staff)
    return _guard(service.update_product, event_id, product_id, body or {}, staff)


@router.delete("/{event_id}/products/{product_id}")
def delete_product(event_id: str, product_id: str, staff=Depends(require_tab("vkpi", "write"))):
    _assert_write(event_id, staff)
    return _guard(service.delete_product, event_id, product_id, staff)
