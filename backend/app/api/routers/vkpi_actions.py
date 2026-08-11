"""V-KPI Action Inbox API (建议、审批、受控执行与人工结果验收)。

GET  /api/admin/vkpi/actions/inbox          — scope 过滤后的今日建议(成员只见自己 owner 的)。
POST /api/admin/vkpi/actions/generate-daily — 跑 8 类生产者、幂等落库(dry_run=true,恒不执行不写业务表)。
红线:建议生成不执行业务动作；真执行必须先批准并通过 W2 双闸，执行结果还需经理附证据验收后才进入学习口径。
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.dependencies.legacy_scope import legacy_system_admin_scope_guard
from app.api.dependencies.manager_guard import require_manager_tab
from app.api.dependencies.perms import require_tab
from app.domains.actions import executors, inbox, reviews

router = APIRouter(prefix="/api/admin/vkpi/actions", tags=["vkpi-actions"])


class ActionReconcileRequest(BaseModel):
    decision: Literal["succeeded", "failed", "unknown"]
    reason: str = Field(min_length=1, max_length=500)
    evidence: list[dict[str, Any]] = Field(min_length=1, max_length=20)
    correlation_id: str = Field(min_length=8, max_length=160)


class ActionResultVerificationRequest(BaseModel):
    decision: Literal["accepted", "rejected"]
    reason: str = Field(min_length=1, max_length=500)
    evidence: list[dict[str, Any]] = Field(min_length=1, max_length=20)
    correlation_id: str = Field(min_length=8, max_length=160)
    expected_execution_ledger_id: int = Field(gt=0)
    expected_detail_sha256: str = Field(min_length=64, max_length=64)
    expected_candidate_sha256: str = Field(min_length=64, max_length=64)

    class Config:
        extra = "forbid"


def _legacy_action_scope(staff: dict[str, Any], *, surface: str) -> dict[str, Any]:
    unavailable = legacy_system_admin_scope_guard(staff, surface=surface)
    if unavailable is not None:
        raise HTTPException(status_code=403, detail=unavailable)
    return staff


def _legacy_action_read(staff=Depends(require_tab("vkpi", "read"))):
    return _legacy_action_scope(staff, surface="Action Inbox")


def _legacy_action_write(staff=Depends(require_tab("vkpi", "write"))):
    return _legacy_action_scope(staff, surface="Action Inbox")


def _legacy_action_manager_read(staff=Depends(require_manager_tab("vkpi", "read"))):
    return _legacy_action_scope(staff, surface="Action result review")


def _legacy_action_manager_write(staff=Depends(require_manager_tab("vkpi", "write"))):
    return _legacy_action_scope(staff, surface="Action result review")


@router.get("/inbox")
def get_action_inbox(
    status: str = Query(default="suggested"),
    category: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    staff=Depends(_legacy_action_read),
) -> dict[str, Any]:
    return inbox.list_inbox(staff, status=status, category=category, limit=limit)


@router.post("/generate-daily")
def generate_daily(
    dry_run: bool = Query(default=True),
    persist: bool = Query(default=True),
    staff=Depends(_legacy_action_write),
) -> dict[str, Any]:
    # W1 恒 dry-run:dry_run 参数保留向前兼容,但生成器不会执行/不写业务表。
    return inbox.generate_daily_action_inbox(staff, dry_run=True, persist=persist)


# ── W2 状态流转 + 执行(write tab;execute 仍需 validators 双闸) ──────────
def _raise_transition_error(res: dict[str, Any]) -> None:
    """状态流转失败统一映 HTTP:不存在/越权 → 404(不泄漏存在性),非法源态 → 409。
    与 mark-done 对齐,只看 HTTP 码的调用方不再把失败当成功(此前恒 200 体内 ok:false)。"""
    reason = str(res.get("reason") or "")
    if reason == "not_found_or_out_of_scope":
        raise HTTPException(status_code=404, detail=reason)
    raise HTTPException(status_code=409, detail=reason)


@router.post("/{action_id}/approve")
def approve(
    action_id: int,
    reason: str = Body(default="", embed=True),
    staff=Depends(_legacy_action_manager_write),
) -> dict[str, Any]:
    # 人审通过 → status=approved;真执行走 /execute,仍受后端 validators 双闸约束。
    # reason 落 approval_reason(批准理由,此前死列)。
    res = inbox.approve_action(action_id, staff, reason=str(reason or ""))
    if not res.get("ok"):
        _raise_transition_error(res)
    return res


@router.post("/{action_id}/dismiss")
def dismiss(
    action_id: int,
    staff=Depends(_legacy_action_write),
) -> dict[str, Any]:
    res = inbox.dismiss_action(action_id, staff)
    if not res.get("ok"):
        _raise_transition_error(res)
    return res


@router.post("/{action_id}/snooze")
def snooze(
    action_id: int,
    minutes: int = Body(default=1440, embed=True, ge=1, le=60 * 24 * 30),
    staff=Depends(_legacy_action_write),
) -> dict[str, Any]:
    res = inbox.snooze_action(action_id, staff, minutes)
    if not res.get("ok"):
        _raise_transition_error(res)
    return res


@router.post("/{action_id}/mark-done")
def mark_done(
    action_id: int,
    note: str = Body(default="", embed=True),
    staff=Depends(_legacy_action_write),
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
    staff=Depends(_legacy_action_manager_write),
) -> dict[str, Any]:
    # 红线:仅 status=='approved' 执行;validators 双闸(approved+touches_v6_fit=False+
    # budget+entity 存在);未审批的写库/LLM 动作返回 outcome='skipped'。
    return executors.execute_action(action_id, staff)


@router.post("/{action_id}/reconcile")
def reconcile_execution(
    action_id: int,
    body: ActionReconcileRequest,
    staff=Depends(_legacy_action_manager_write),
) -> dict[str, Any]:
    """Manually settle an uncertain external execution with auditable evidence."""
    res = inbox.reconcile_executing_action(
        action_id,
        staff,
        decision=body.decision,
        reason=body.reason,
        evidence=body.evidence,
        correlation_id=body.correlation_id,
    )
    if res.get("ok"):
        return res
    reason = str(res.get("reason") or "reconciliation_failed")
    if reason == "not_found_or_out_of_scope":
        raise HTTPException(status_code=404, detail=reason)
    if reason in {
        "invalid_reconciliation_decision",
        "reconciliation_reason_required",
        "reconciliation_evidence_required",
        "reconciliation_correlation_required",
        "reconciliation_actor_required",
    }:
        raise HTTPException(status_code=422, detail=reason)
    if reason in {
        "reconciliation_correlation_conflict",
        "action_not_awaiting_reconciliation",
        "reconciliation_state_changed",
    }:
        raise HTTPException(status_code=409, detail=reason)
    raise HTTPException(status_code=503, detail=reason)


@router.post("/{action_id}/verify-result")
def verify_execution_result(
    action_id: int,
    body: ActionResultVerificationRequest,
    staff=Depends(_legacy_action_manager_write),
) -> dict[str, Any]:
    """Manager-only evidence gate between tool execution and learning evidence."""
    scope_unavailable = legacy_system_admin_scope_guard(staff, surface="Action result review")
    if scope_unavailable is not None:
        raise HTTPException(status_code=403, detail=scope_unavailable)
    res = reviews.verify_action_result(
        action_id,
        staff,
        decision=body.decision,
        reason=body.reason,
        evidence=body.evidence,
        correlation_id=body.correlation_id,
        expected_execution_ledger_id=body.expected_execution_ledger_id,
        expected_detail_sha256=body.expected_detail_sha256,
        expected_candidate_sha256=body.expected_candidate_sha256,
    )
    if res.get("ok"):
        return res
    reason = str(res.get("reason") or "action_result_verification_failed")
    if reason == "action_not_found":
        raise HTTPException(status_code=404, detail=reason)
    if reason in {
        "invalid_verification_decision", "verification_reason_required",
        "verification_evidence_required", "verification_correlation_required",
        "verification_actor_required", "verification_candidate_required",
    }:
        raise HTTPException(status_code=422, detail=reason)
    if reason == "verification_scope_unavailable":
        raise HTTPException(status_code=403, detail=reason)
    if reason in {
        "verification_correlation_conflict", "action_not_awaiting_result_verification",
        "action_result_already_verified", "successful_execution_receipt_required",
        "action_verification_state_changed", "ambiguous_agent_tool_run_receipts",
        "verification_candidate_changed",
    }:
        raise HTTPException(status_code=409, detail=reason)
    raise HTTPException(status_code=503, detail=reason)


@router.get("/{action_id}/review-candidate")
def action_review_candidate(
    action_id: int,
    staff=Depends(_legacy_action_manager_read),
) -> dict[str, Any]:
    """Return the redacted, hash-bound execution receipt a manager will judge."""
    del staff
    result = reviews.get_action_review_candidate(action_id)
    if result.get("ok"):
        result.pop("ok", None)
        return result
    reason = str(result.get("reason") or "review_candidate_unavailable")
    if reason == "action_not_found":
        raise HTTPException(status_code=404, detail=reason)
    if reason in {
        "action_not_awaiting_result_verification", "action_result_already_verified",
        "successful_execution_receipt_required", "execution_receipt_not_reviewable",
        "ambiguous_agent_tool_run_receipts",
    }:
        raise HTTPException(status_code=409, detail=reason)
    raise HTTPException(status_code=503, detail=reason)


# ── R7 执行台账回读(read tab;只读 vkpi_action_execution_ledger,scope 同 inbox) ──
@router.get("/ledger/recent")
def recent_execution_ledger(
    limit: int = Query(default=50, ge=1, le=200),
    staff=Depends(_legacy_action_read),
) -> dict[str, Any]:
    """最近 N 条执行台账(成员仅自己 owner 的 action;管理层全局)。"""
    return inbox.read_execution_ledger(staff, action_id=None, limit=limit)


@router.get("/{action_id}/ledger")
def action_execution_ledger(
    action_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    staff=Depends(_legacy_action_read),
) -> dict[str, Any]:
    """单条 action 的所有执行台账行(before/after 验收;越权/不存在 → 空)。"""
    return inbox.read_execution_ledger(staff, action_id=action_id, limit=limit)
