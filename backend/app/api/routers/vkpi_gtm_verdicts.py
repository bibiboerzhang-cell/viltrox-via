"""V-KPI GTM 强制裁决流路由(闭环波 L2,规格第四章)。

- GET  /api/admin/vkpi/gtm/verdicts/pending
  → 待裁决清单:review_at 到期且未 finalized 的 bet(带裁决任务与 open 结果行线索)。
- POST /api/admin/vkpi/gtm/verdicts/{verdict_id}/decide
  → 人工裁决唯一入口:写 vkpi_gtm_outcomes 的 decision+lesson(+next_weight_change),
    decided 即 finalized。{verdict_id} 缺省按 bet 的 action_inbox id 解析(pending 列表
    的 id 字段),body.id_type='outcome' 时按 vkpi_gtm_outcomes.id 解析。
- GET  /api/admin/vkpi/gtm/outcomes
  → GTM 结果总账只读列表(可按 decision 过滤,附 finalized 计数)。

实现在 app.domains.market_brain.verdict_flow。
诚实态:表未建(迁移 141/217 未 apply)domain 层回明确 reason 不 500;
错误按语义回码:非法 decision 422 / 找不到 404 / 已裁决 409 / 无 staff 403 / 缺迁移 503。
红线:裁决只能人工 POST(domain 层强制 staff id),绝无自动裁决路径;
权重回流只做结构化记录,真生效走既有 recommendation_feedback 链(L4);
零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.dependencies.gtm_scope import legacy_gtm_scope_guard
from app.api.dependencies.manager_guard import require_manager_staff, require_manager_tab
from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-gtm-verdicts"])


class DecideBody(BaseModel):
    decision: str = Field(..., description="validated / failed / partial / retry / escalate / retreat")
    lesson: str = Field(default="", max_length=2000, description="一句话教训(裁决时人工写)")
    weight_change: dict[str, Any] | None = Field(
        default=None,
        description="结构化权重调整条目(仅记录进 next_weight_change;真回流 L4 走 recommendation_feedback 链)",
    )
    id_type: str = Field(default="inbox", description="路径 id 的口径:inbox=bet 的 action_inbox id(缺省)/ outcome=vkpi_gtm_outcomes.id")


class OutreachTruthBindingBody(BaseModel):
    correlation_id: str = Field(min_length=8, max_length=160)

    class Config:
        extra = "forbid"


class OutreachReplyVerificationBody(BaseModel):
    outcome: Literal["replied", "no_reply"]
    correlation_id: str = Field(min_length=8, max_length=160)
    expected_candidate_sha256: str = Field(min_length=64, max_length=64)
    candidate_observed_at: str = Field(min_length=20, max_length=80)

    class Config:
        extra = "forbid"


_FAIL_STATUS = {
    "invalid_decision": 422,
    "invalid_id_type": 422,
    "missing_id": 422,
    "verdict_task_missing_bet_ref": 422,
    "outcome_not_found": 404,
    "inbox_not_found": 404,
    "bet_inbox_not_found": 404,
    "already_decided": 409,
    "staff_required": 403,
    "migration_141_not_applied": 503,
    "migration_217_not_applied": 503,
    "prediction_observation_window_not_ready": 409,
}


_OUTREACH_BINDING_STATUS = {
    "outreach_binding_scope_unavailable": 403,
    "outreach_action_not_found": 404,
    "outreach_binding_not_found": 404,
    "outreach_prediction_not_found": 404,
    "outreach_kol_pool_not_found": 404,
    "outreach_project_not_found": 404,
    "outreach_action_already_bound": 409,
    "outreach_binding_correlation_conflict": 409,
    "outreach_binding_event_conflict": 409,
    "outreach_project_ambiguous": 409,
    "outreach_binding_correlation_required": 422,
    "outreach_binding_ids_required": 422,
    "outreach_action_not_approved_gtm_bet": 422,
    "outreach_action_approval_proof_invalid": 422,
    "outreach_prediction_contract_invalid": 422,
    "outreach_kol_link_missing": 422,
    "outreach_kol_channel_mismatch": 422,
    "outreach_project_scope_not_found": 422,
    "outreach_first_outbound_not_observed": 422,
    "outreach_outbound_precedes_approval": 422,
    "outreach_outbound_evidence_unverified": 422,
    "outreach_server_clock_unavailable": 503,
    "outreach_binding_schema_unavailable": 503,
    "outreach_binding_write_failed": 503,
    "outreach_binding_status_unavailable": 503,
}


_OUTREACH_REPLY_STATUS = {
    "outreach_reply_scope_unavailable": 403,
    "outreach_reply_binding_not_found": 404,
    "outreach_reply_already_verified": 409,
    "outreach_reply_correlation_conflict": 409,
    "outreach_reply_event_conflict": 409,
    "outreach_reply_exists": 409,
    "outreach_reply_candidate_changed": 409,
    "outreach_no_reply_window_open": 409,
    "outreach_reply_binding_required": 422,
    "outreach_reply_outcome_invalid": 422,
    "outreach_reply_correlation_required": 422,
    "outreach_reply_candidate_required": 422,
    "outreach_reply_binding_proof_invalid": 422,
    "outreach_verified_inbound_not_observed": 422,
    "outreach_inbound_content_unreviewable": 422,
    "outreach_outbound_content_unreviewable": 422,
    "outreach_reply_schema_unavailable": 503,
    "outreach_reply_server_clock_unavailable": 503,
    "outreach_reply_write_failed": 503,
}


@router.post("/gtm/actions/{action_inbox_id}/outreach-binding")
def bind_action_outreach_truth(
    action_inbox_id: int,
    body: OutreachTruthBindingBody,
    staff=Depends(require_manager_tab("vkpi", "write")),
) -> dict:
    """Bind an approved Action to the only server-resolved project/outbound."""
    scope_unavailable = legacy_gtm_scope_guard(staff, surface="GTM outreach truth binding")
    if scope_unavailable is not None:
        raise HTTPException(status_code=403, detail=scope_unavailable)
    # Keep an explicit domain-adjacent guard as defense in depth for direct
    # handler calls in internal tooling; ordinary employees may never bind truth.
    require_manager_staff(staff)
    from app.domains.market_brain import outreach_truth_bridge

    result = outreach_truth_bridge.create_outreach_binding(
        int(action_inbox_id),
        correlation_id=body.correlation_id,
        staff=staff,
    )
    if result.get("ok"):
        return result
    reason = str(result.get("reason") or "outreach_binding_write_failed")
    raise HTTPException(
        status_code=_OUTREACH_BINDING_STATUS.get(reason, 400),
        detail=reason,
    )


@router.get("/gtm/actions/{action_inbox_id}/outreach-binding-status")
def get_action_outreach_binding_status(
    action_inbox_id: int,
    staff=Depends(require_manager_tab("vkpi", "read")),
) -> dict:
    """Recover the proof-valid binding/reply status after refresh or handoff."""
    scope_unavailable = legacy_gtm_scope_guard(staff, surface="GTM outreach truth status")
    if scope_unavailable is not None:
        raise HTTPException(status_code=403, detail=scope_unavailable)
    require_manager_staff(staff)
    from app.domains.market_brain import outreach_truth_bridge

    result = outreach_truth_bridge.get_outreach_binding_status(
        int(action_inbox_id), staff=staff,
    )
    if result.get("ok"):
        return result
    reason = str(result.get("reason") or "outreach_binding_status_unavailable")
    raise HTTPException(
        status_code=_OUTREACH_BINDING_STATUS.get(reason, 400),
        detail=reason,
    )


@router.get("/gtm/outreach-bindings/{binding_id}/reply-review-candidate")
def get_action_outreach_reply_candidate(
    binding_id: int,
    outcome: Literal["replied", "no_reply"] = Query(...),
    staff=Depends(require_manager_tab("vkpi", "read")),
) -> dict:
    """Return the exact redacted/hash-bound reply snapshot a manager may sign."""
    scope_unavailable = legacy_gtm_scope_guard(staff, surface="GTM outreach reply review")
    if scope_unavailable is not None:
        raise HTTPException(status_code=403, detail=scope_unavailable)
    require_manager_staff(staff)
    from app.domains.market_brain import outreach_reply_truth

    result = outreach_reply_truth.get_reply_review_candidate(
        int(binding_id), outcome=outcome, staff=staff,
    )
    if result.get("ok"):
        return result
    reason = str(result.get("reason") or "outreach_reply_candidate_unavailable")
    raise HTTPException(status_code=_OUTREACH_REPLY_STATUS.get(reason, 503), detail=reason)


@router.post("/gtm/outreach-bindings/{binding_id}/reply-verification")
def verify_action_outreach_reply(
    binding_id: int,
    body: OutreachReplyVerificationBody,
    staff=Depends(require_manager_tab("vkpi", "write")),
) -> dict:
    """Append a manager-bound replied/no-reply receipt; never sends outreach."""
    scope_unavailable = legacy_gtm_scope_guard(staff, surface="GTM outreach reply truth")
    if scope_unavailable is not None:
        raise HTTPException(status_code=403, detail=scope_unavailable)
    require_manager_staff(staff)
    from app.domains.market_brain import outreach_reply_truth

    result = outreach_reply_truth.verify_reply(
        int(binding_id),
        outcome=body.outcome,
        correlation_id=body.correlation_id,
        expected_candidate_sha256=body.expected_candidate_sha256,
        candidate_observed_at=body.candidate_observed_at,
        staff=staff,
    )
    if result.get("ok"):
        return result
    reason = str(result.get("reason") or "outreach_reply_write_failed")
    raise HTTPException(status_code=_OUTREACH_REPLY_STATUS.get(reason, 400), detail=reason)


@router.get("/gtm/outreach-truth/coverage")
def get_outreach_truth_coverage(
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Expose due/bound/actual coverage; unverified censors never raise claims."""
    scope_unavailable = legacy_gtm_scope_guard(staff, surface="GTM outreach truth coverage")
    if scope_unavailable is not None:
        return scope_unavailable
    from app.domains.market_brain import outreach_truth_bridge

    return outreach_truth_bridge.outreach_prediction_coverage()


@router.get("/gtm/verdicts/pending")
def get_pending_verdicts(
    limit: int = Query(default=50, ge=1, le=200, description="最多返回的待裁决条数"),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """待裁决清单(全只读):到期未裁决的 bet + 裁决任务/结果行线索。"""
    scope_unavailable = legacy_gtm_scope_guard(staff, surface="GTM pending verdicts")
    if scope_unavailable is not None:
        return {**scope_unavailable, "items": [], "count": 0}
    from app.domains.market_brain import verdict_flow

    try:
        return verdict_flow.list_pending_verdicts(limit=limit)
    except Exception as exc:  # noqa: BLE001 — 读端失败不炸接口,诚实回原因
        logger.warning("gtm_verdicts.pending failed: %s", exc)
        return {"status": "error", "reason": str(exc)[:300], "items": [], "count": 0}


@router.post("/gtm/verdicts/{verdict_id}/decide")
def decide_verdict(
    verdict_id: int,
    body: DecideBody,
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """人工裁决(唯一写 decision 的入口):decided 即 finalized,已裁决行拒绝改判。

    管理层闸(owner+manager):裁决直接改判权重回流,员工 vkpi:write 不够 → 403。
    """
    scope_unavailable = legacy_gtm_scope_guard(staff, surface="GTM verdict decision")
    if scope_unavailable is not None:
        return scope_unavailable
    require_manager_staff(staff)
    from app.domains.market_brain import verdict_flow

    id_type = str(body.id_type or "inbox").strip().lower()
    if id_type not in ("inbox", "outcome"):
        raise HTTPException(status_code=422, detail="id_type must be 'inbox' or 'outcome'")

    result = verdict_flow.record_verdict(
        outcome_id=int(verdict_id) if id_type == "outcome" else None,
        inbox_id=int(verdict_id) if id_type == "inbox" else None,
        decision=body.decision,
        lesson=body.lesson,
        weight_change=body.weight_change,
        staff=staff,
    )
    if not result.get("ok"):
        reason = str(result.get("reason") or "verdict_failed")
        raise HTTPException(status_code=_FAIL_STATUS.get(reason, 400), detail=result)
    return result


@router.get("/gtm/outcomes")
def get_gtm_outcomes(
    decision: str | None = Query(default=None, description="按裁决过滤:open 或六种裁决之一;缺省全量"),
    limit: int = Query(default=50, ge=1, le=200, description="最多返回的结果行数"),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """GTM 结果总账只读列表(附 by_decision 分布与 finalized 计数)。"""
    scope_unavailable = legacy_gtm_scope_guard(staff, surface="GTM outcomes")
    if scope_unavailable is not None:
        return {**scope_unavailable, "items": [], "count": 0}
    from app.domains.market_brain import verdict_flow

    try:
        return verdict_flow.list_outcomes(decision=decision, limit=limit)
    except Exception as exc:  # noqa: BLE001 — 读端失败不炸接口,诚实回原因
        logger.warning("gtm_verdicts.outcomes failed: %s", exc)
        return {"status": "error", "reason": str(exc)[:300], "items": [], "count": 0}
