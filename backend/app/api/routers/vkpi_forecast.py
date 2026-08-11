"""V-KPI KOL 预测战绩路由(件 A · 档案第八层)。

- GET /api/admin/vkpi/kol-pool/{kol_pool_id}/forecast?sku=(可选)
  → 「找他合作,大概能跑出什么数」:基于历史 evidence 播放分布的
  p10/p50/p90 期望播放 + 互动率中位数 + 带品实绩小结(track_record)。
  实现在 app.domains.kol.performance_forecast(决定性分位数法,零 LLM、显式 dry-run 零写库)。

诚实态:KOL 不存在 404;0 样本 status="empty";样本<3 confidence="low";
预测数字全部可追溯(basis 带 method/样本量/时间窗/调整因子)。
聚合内部异常不 500,回 {status:"error", reason}(前端安静缺席,增益块非阻塞)。
红线:纯读展示,零触 fit 评分存储列、不碰 rule_v0、零 LLM 调用。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.legacy_scope import legacy_system_admin_scope_guard
from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-forecast"])


def _require_legacy_forecast_scope(staff: dict[str, Any]) -> None:
    """Fail closed until the legacy forecast/KOL tables are tenant-keyed."""
    unavailable = legacy_system_admin_scope_guard(staff, surface="KOL forecast")
    if unavailable is not None:
        raise HTTPException(status_code=403, detail=unavailable)


@router.get("/kol-pool/{kol_pool_id}/forecast")
def get_kol_forecast(
    kol_pool_id: int,
    sku: str | None = Query(default=None, max_length=120, description="可选 SKU:带上则用焦段覆盖史做调整系数"),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """KOL 预测战绩:期望播放三档区间 + 置信度 + 带品实绩(全只读,不写库)。"""
    _require_legacy_forecast_scope(staff)
    from app.domains.kol import performance_forecast

    sku_clean = (sku or "").strip() or None
    try:
        return performance_forecast.forecast_for_kol(
            int(kol_pool_id),
            sku_clean,
            dry_run=True,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 增益块失败不炸接口,诚实回原因
        logger.warning("forecast_for_kol failed for kol_pool_id=%s sku=%s: %s", kol_pool_id, sku_clean, exc)
        return {"status": "error", "reason": str(exc)[:300], "kol_pool_id": int(kol_pool_id)}
