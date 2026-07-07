"""V-KPI Action Inbox API (W1 · dry-run only).

GET  /api/admin/vkpi/actions/inbox          — scope 过滤后的今日建议(成员只见自己 owner 的)。
POST /api/admin/vkpi/actions/generate-daily — 跑 8 类生产者、幂等落库(dry_run=true,恒不执行不写业务表)。
红线:这两个端点都不会触发任何业务写或 LLM 执行;execute 走 W2 的 approve/执行器(尚未开放)。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.domains.actions import executors, inbox

router = APIRouter(prefix="/api/admin/vkpi/actions", tags=["vkpi-actions"])


@router.get("/inbox")
def get_action_inbox(
    status: str = Query(default="suggested"),
    category: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    return inbox.list_inbox(staff, status=status, category=category, limit=limit)


@router.post("/generate-daily")
def generate_daily(
    dry_run: bool = Query(default=True),
    persist: bool = Query(default=True),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    # W1 恒 dry-run:dry_run 参数保留向前兼容,但生成器不会执行/不写业务表。
    return inbox.generate_daily_action_inbox(staff, dry_run=True, persist=persist)


# ── W2 状态流转 + 执行(write tab;execute 仍需 validators 双闸) ──────────
@router.post("/{action_id}/approve")
def approve(
    action_id: int,
    reason: str = Body(default="", embed=True),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    # 人审通过 → status=approved;真执行走 /execute,仍受后端 validators 双闸约束。
    # reason 落 approval_reason(批准理由,此前死列)。
    return inbox.approve_action(action_id, staff, reason=str(reason or ""))


@router.post("/{action_id}/dismiss")
def dismiss(
    action_id: int,
    staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    return inbox.dismiss_action(action_id, staff)


@router.post("/{action_id}/snooze")
def snooze(
    action_id: int,
    minutes: int = Body(default=1440, embed=True, ge=1, le=60 * 24 * 30),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    return inbox.snooze_action(action_id, staff, minutes)


@router.post("/{action_id}/mark-done")
def mark_done(
    action_id: int,
    note: str = Body(default="", embed=True),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    # 人工已执行:仅 approved 可转 executed(suggested_endpoint 为空的动作在系统外做完后收口)。
    # 越权/不存在 → 404(scope 不泄漏存在性);非法源态 → 409。落 manual_execution 台账。
    res = inbox.mark_done_action(action_id, staff, note=(str(note or "").strip() or None))
    if not res.get("ok"):
        reason = str(res.get("reason") or "")
        if reason == "not_found_or_out_of_scope":
            raise HTTPException(status_code=404, detail=reason)
        raise HTTPException(status_code=409, detail=reason)
    return res


@router.post("/{action_id}/execute")
def execute(
    action_id: int,
    staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    # 红线:仅 status=='approved' 执行;validators 双闸(approved+touches_v6_fit=False+
    # budget+entity 存在);未审批的写库/LLM 动作返回 outcome='skipped'。
    return executors.execute_action(action_id, staff)


# ── R7 执行台账回读(read tab;只读 vkpi_action_execution_ledger,scope 同 inbox) ──
@router.get("/ledger/recent")
def recent_execution_ledger(
    limit: int = Query(default=50, ge=1, le=200),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """最近 N 条执行台账(成员仅自己 owner 的 action;管理层全局)。"""
    return inbox.read_execution_ledger(staff, action_id=None, limit=limit)


@router.get("/{action_id}/ledger")
def action_execution_ledger(
    action_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """单条 action 的所有执行台账行(before/after 验收;越权/不存在 → 空)。"""
    return inbox.read_execution_ledger(staff, action_id=action_id, limit=limit)
