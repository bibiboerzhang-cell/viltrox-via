"""V-KPI projects router: fulfillment / observation endpoints sub-router.

行为不变搬迁(从 vkpi_projects.py 整组 move):履约观测 + 观察窗口 + 内容帖候选 +
履约 sweep + 发货审批 + advance-retrospective 这一内聚簇整组搬到本 sibling 子 router
(router=APIRouter() 无 prefix),原文件 router.include_router(_sub) 兜住;
函数体逐字不变,lazy import 原样保留。
红线:此模块零 fit 写。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query

from app.api.dependencies.perms import require_tab
from app.domains.projects import retrospective_aggregate

router = APIRouter()


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


@router.post("/projects/{project_id}/content-posts")
def add_content_candidate(
    project_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
):
    """履约内容帖子(手动喂数):操作员手录 KOL 已发布内容 URL → candidate 入库等复核。

    本地/无抓取时的内容闭环入口——scan_windows_for_content 走抓取(GFW 下不可用)时用此手录。
    body: {assignment_id?, kol_pool_id?, content_url(必填), match_confidence?, published_at?, evidence_id?}。
    红线:只写 vkpi_project_content_posts(status='candidate' 等人复核),绝不改项目/派单/费用/fit。
    """
    from app.domains.projects import observation_windows

    b = body or {}
    return observation_windows.record_content_candidate(
        int(project_id),
        b.get("assignment_id"),
        b.get("kol_pool_id"),
        {k: b.get(k) for k in ("content_url", "match_confidence", "published_at", "evidence_id")},
        staff=staff,
    )


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


@router.get("/projects/pipeline-readiness")
def pipeline_readiness(staff=Depends(require_tab("vkpi", "read"))):
    """R20 · 数据主链路 5 个手动断点的「待接」就绪度概览(只读;每环推进仍走各自人审端点)。"""
    from app.domains.projects import pipeline_sequence

    return pipeline_sequence.pipeline_readiness(staff=staff)


@router.post("/projects/fulfillment-sweep")
def fulfillment_sweep(staff=Depends(require_tab("vkpi", "write"))):
    """P2 落业务 · 履约 Durable Workflow:物流同步→签收开窗→扫内容(可恢复,每步入事件流)。"""
    from app.domains.projects import fulfillment_workflow

    return fulfillment_workflow.start_fulfillment_sweep(staff)


@router.post("/projects/fulfillment-sweep/{run_id}/resume")
def fulfillment_sweep_resume(run_id: int, staff=Depends(require_tab("vkpi", "write"))):
    """续跑失败/暂停的履约 run(从 current_step 接上,不重跑前序)。"""
    from app.domains.projects import fulfillment_workflow

    return fulfillment_workflow.resume_fulfillment_sweep(int(run_id), staff)


@router.get("/projects/shipping-approvals")
def list_shipping_approvals(
    status: str = "pending",
    limit: int = 100,
    staff=Depends(require_tab("vkpi", "read")),
):
    """发货审批门槛:列待审/已审发货条目(成员只看自己请求的,管理层全看)。"""
    from app.domains.projects import shipment_approval

    return shipment_approval.list_pending(staff, status=status, limit=int(limit))


@router.post("/projects/{project_id}/kols/{kol_pool_id}/shipping/request")
def request_shipping_approval(
    project_id: int,
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
):
    """成员请求发货(进 pending 待管理员人审)。"""
    from app.domains.projects import shipment_approval

    return shipment_approval.request_approval(project_id, kol_pool_id, staff=staff, reason=str((body or {}).get("reason") or ""))


@router.post("/projects/{project_id}/kols/{kol_pool_id}/shipping/approve")
def approve_shipping(
    project_id: int,
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
):
    """管理层通过发货(非管理层 → admin_only)。"""
    from app.domains.projects import shipment_approval

    return shipment_approval.approve(project_id, kol_pool_id, staff=staff, reason=str((body or {}).get("reason") or ""))


@router.post("/projects/{project_id}/kols/{kol_pool_id}/shipping/reject")
def reject_shipping(
    project_id: int,
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
):
    """管理层驳回发货。"""
    from app.domains.projects import shipment_approval

    return shipment_approval.reject(project_id, kol_pool_id, staff=staff, reason=str((body or {}).get("reason") or ""))


@router.post("/projects/{project_id}/content-posts/advance-retrospective")
def advance_content_posts_to_retrospective(
    project_id: int,
    staff=Depends(require_tab("vkpi", "write")),
):
    """R12 · 把项目下 matched 内容帖批量推进 retrospective_ready,并 enqueue 复盘聚合(只排队,不跑 LLM)。

    仅触 content_posts.status + enqueue apify_jobs;绝不改 project.stage/cost/fit。无 matched 帖则不 enqueue。
    """
    from app.domains.projects import observation_windows

    advanced = observation_windows.advance_matched_posts_to_retrospective_ready(project_id, staff=staff)
    if str(advanced.get("status")) != "ok":
        return advanced
    result: dict = {**advanced, "retrospective": None}
    if int(advanced.get("advanced_count") or 0) > 0:
        try:
            result["retrospective"] = retrospective_aggregate.enqueue_project_retrospective(project_id, staff=staff)
        except Exception as exc:  # enqueue 失败不回滚 advance(帖已 ready,可手动再触发复盘)
            result["retrospective_error"] = str(exc)[:200]
    return result
