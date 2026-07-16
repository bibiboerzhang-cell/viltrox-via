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

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.dependencies.gtm_scope import legacy_gtm_scope_guard
from app.api.dependencies.manager_guard import require_manager_staff
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
}


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
