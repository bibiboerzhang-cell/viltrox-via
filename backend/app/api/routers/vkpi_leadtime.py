"""件②:制作周期 + 竞业监控 只读端点(prefix=/api/admin/vkpi)。

GET /kol-pool/{id}/production-leadtime — 签收→发布历史间隔(观察窗口链 + 阶段事件)
GET /kol-pool/{id}/competing-activity  — 近 90 天竞品露出(final_v1 深析产物 + brand_signal)

两端点均 require_tab("vkpi","read");聚合逻辑在 app.domains.kol.leadtime_competing
(懒 import)。均为纯只读增益信号:KOL 不存在回 404;聚合内部异常不 500,
诚实回 {status:'error', reason}(前端小卡非阻塞,安静降级)。
红线:零写库、零新采集、零 LLM;绝不触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-leadtime"])


@router.get("/kol-pool/{kol_pool_id}/production-leadtime")
def get_production_leadtime(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """制作周期:该 KOL 历史「签收→发布」间隔(median_days + samples);样本 <1 诚实 empty。"""
    del staff
    from app.domains.kol import leadtime_competing

    try:
        return leadtime_competing.production_leadtime(int(kol_pool_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 增益聚合失败不该炸接口,诚实回原因
        return {"status": "error", "reason": str(exc)[:300], "kol_pool_id": int(kol_pool_id)}


@router.get("/kol-pool/{kol_pool_id}/competing-activity")
def get_competing_activity(
    kol_pool_id: int,
    window_days: int = Query(90, ge=7, le=365),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """竞业监控:近 window_days 天(默认 90)该 KOL 内容中的竞品露出;无露出诚实 empty。"""
    del staff
    from app.domains.kol import leadtime_competing

    try:
        return leadtime_competing.competing_activity(int(kol_pool_id), window_days=int(window_days))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 增益聚合失败不该炸接口,诚实回原因
        return {"status": "error", "reason": str(exc)[:300], "kol_pool_id": int(kol_pool_id)}
