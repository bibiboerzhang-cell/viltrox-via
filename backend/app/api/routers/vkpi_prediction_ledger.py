"""V-KPI 预测台账：只读聚合 + 经理从已终结证据录入 actual。

- GET /api/admin/vkpi/prediction-ledger/summary
  → 全部分组台账摘要(kol_recommend/market_bet/alert_signal/brand_signal/performance_forecast
    + 执行台账动态 category 组),每组命中率/样本数/置信度/basis。
- GET /api/admin/vkpi/prediction-ledger/{action_type}?window=20
  → 单组近 window 次命中率(契约键 status/hit_rate/sample_count/confidence/basis)。
  注意:/summary 必须先于 /{action_type} 声明,否则会被路径参数吞掉。

实现在 app.domains.agents.prediction_ledger(纯 SQL 聚合已有数据,零 LLM、零写库)。
诚实态:数据荒是常态,空组 sample_count=0 照实返回;domain 层永不 raise,
路由层再兜一层,聚合异常不 500,回 {status:"error", reason}。
红线:不执行外部动作；写 actual 时客户端不能提供数值，后端只从已终结
outcome 的指定证据读取并校验产品、市场、渠道、周期与时间顺序；永不回写
fit 评分存储列，也不碰 rule_v0。
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.dependencies.gtm_scope import legacy_gtm_scope_guard
from app.api.dependencies.manager_guard import require_manager_tab
from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-prediction-ledger"])


class PredictionActualBody(BaseModel):
    outcome_id: int = Field(ge=1)
    evidence_field: Literal["actual_result", "window_7d", "window_14d", "window_28d"]
    metric_path: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.-]+$")
    correlation_id: str = Field(min_length=8, max_length=160)
    notes: str | None = Field(default=None, max_length=1000)

    class Config:
        extra = "forbid"


@router.get("/prediction-ledger/summary")
def get_prediction_ledger_summary(
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """预测台账摘要:每组命中率条 + 样本数 + 置信度(全只读,不写库)。"""
    scope_unavailable = legacy_gtm_scope_guard(staff, surface="Prediction ledger summary")
    if scope_unavailable is not None:
        raise HTTPException(status_code=403, detail=scope_unavailable)

    from app.domains.agents import prediction_ledger

    try:
        return prediction_ledger.ledger_summary()
    except Exception as exc:  # noqa: BLE001 — domain 层已兜底,这里是最后保险,不 500
        logger.warning("prediction_ledger summary route failed: %s", exc)
        return {"status": "error", "reason": str(exc)[:300], "groups": []}


@router.post("/prediction-ledger/runs/{run_id}/actual-from-outcome")
def record_prediction_actual(
    run_id: str,
    body: PredictionActualBody,
    staff=Depends(require_manager_tab("vkpi", "write")),
) -> dict:
    """Record an eval using a server-resolved value from finalized evidence."""
    scope_unavailable = legacy_gtm_scope_guard(staff, surface="Prediction actual review")
    if scope_unavailable is not None:
        raise HTTPException(status_code=403, detail=scope_unavailable)
    from app.domains.market_brain import prediction_ledger

    result = prediction_ledger.record_eval_from_finalized_outcome(
        run_id,
        staff=staff,
        outcome_id=body.outcome_id,
        evidence_field=body.evidence_field,
        metric_path=body.metric_path,
        correlation_id=body.correlation_id,
        notes=body.notes,
    )
    if result.get("ok"):
        return result
    reason = str(result.get("reason") or "prediction_eval_failed")
    if reason in {"outcome_not_found", "run_not_found", "actual_metric_not_found"}:
        raise HTTPException(status_code=404, detail=reason)
    if reason in {
        "invalid_actual_binding", "outcome_not_finalized", "outcome_missing_observed_evidence",
        "actual_metric_not_numeric", "actual_evidence_binding_required",
        "actual_correlation_required", "actual_product_sku_mismatch",
        "actual_market_mismatch", "actual_channel_mismatch", "actual_horizon_mismatch",
        "actual_chronology_invalid", "prediction_evaluation_contract_missing",
        "actual_observation_anchor_invalid",
        "actual_task_mismatch", "actual_action_mismatch", "actual_metric_contract_mismatch",
        "actual_window_not_closed", "actual_notes_invalid",
    }:
        raise HTTPException(status_code=422, detail=reason)
    if reason == "actual_scope_unavailable":
        raise HTTPException(status_code=403, detail=reason)
    if reason in {"actual_evidence_conflict", "actual_outcome_mismatch"}:
        raise HTTPException(status_code=409, detail=reason)
    if reason == "table_missing" or reason == "outcome_table_missing":
        raise HTTPException(status_code=503, detail=reason)
    raise HTTPException(status_code=503, detail=reason)


@router.get("/prediction-ledger/{action_type}")
def get_prediction_ledger_group(
    action_type: str,
    window: int = Query(default=20, ge=1, le=200),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """单组近 window 次命中率;未知 action_type 诚实返回 unknown_action_type,不 404 不编数。"""
    scope_unavailable = legacy_gtm_scope_guard(staff, surface="Prediction ledger group")
    if scope_unavailable is not None:
        raise HTTPException(status_code=403, detail=scope_unavailable)

    from app.domains.agents import prediction_ledger

    try:
        return prediction_ledger.hit_rate_for(str(action_type), window=int(window))
    except Exception as exc:  # noqa: BLE001 — 最后保险,不 500
        logger.warning("prediction_ledger group route failed action_type=%s: %s", action_type, exc)
        return {"status": "error", "reason": str(exc)[:300], "action_type": str(action_type)}
