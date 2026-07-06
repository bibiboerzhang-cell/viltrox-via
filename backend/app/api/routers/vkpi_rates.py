"""V-KPI KOL 报价卡路由(件B)。

- GET  /api/admin/vkpi/kol-pool/{kol_pool_id}/rates          已录报价列表(read)
- POST /api/admin/vkpi/kol-pool/{kol_pool_id}/rates          手动录一条报价(write)
- GET  /api/admin/vkpi/kol-pool/{kol_pool_id}/rate-estimate  估价区间(read)

实现在 app.domains.kol.rate_card(懒 import):有真报价行走中位数
(recorded_rates_median_v0),无行走 CPM 行业基准兜底(cpm_benchmark_v0, confidence=low),
表未建不炸。估价数字带 basis/method/confidence 可追溯,数据不足诚实空态。

诚实态:KOL 不存在 404;录入校验失败 400;聚合内部异常不 500,
回 {status:"error", reason}(前端安静降级,增益块非阻塞)。
红线:仅写 vkpi_kol_rates 一表(迁移211);零 LLM;零触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-rates"])


@router.get("/kol-pool/{kol_pool_id}/rates")
def list_kol_rates(
    kol_pool_id: int,
    limit: int = Query(100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """该 KOL 已录报价列表(新在前);表未建/无报价诚实 empty。"""
    del staff
    from app.domains.kol import rate_card

    try:
        return rate_card.list_rates(int(kol_pool_id), limit=int(limit))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 增益块失败不炸接口,诚实回原因
        logger.warning("list_rates failed for kol_pool_id=%s: %s", kol_pool_id, exc)
        return {"status": "error", "reason": str(exc)[:300], "kol_pool_id": int(kol_pool_id)}


@router.post("/kol-pool/{kol_pool_id}/rates")
def add_kol_rate(
    kol_pool_id: int,
    body: dict[str, Any],
    staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    """手动录一条报价(amount_usd 必填;source 缺省 negotiation;confidence 缺省按来源推)。"""
    from app.domains.kol import rate_card

    staff_id = None
    try:
        staff_id = int((staff or {}).get("id") or 0) or None
    except Exception:  # noqa: BLE001 — staff 形态兜底,录入不因取 id 失败而挡
        staff_id = None

    try:
        return rate_card.add_rate(int(kol_pool_id), body or {}, staff_id=staff_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 写失败(如迁移211未 apply)诚实回原因,不 500
        logger.warning("add_rate failed for kol_pool_id=%s: %s", kol_pool_id, exc)
        return {"status": "error", "reason": str(exc)[:300], "kol_pool_id": int(kol_pool_id)}


@router.get("/kol-pool/{kol_pool_id}/rate-estimate")
def get_kol_rate_estimate(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """估价区间:真报价中位数优先,无报价 CPM 基准兜底;KOL 不存在 404。"""
    del staff
    from app.domains.kol import rate_card

    result = rate_card.estimate_rate(int(kol_pool_id))
    if str(result.get("status") or "") == "not_found":
        raise HTTPException(status_code=404, detail=str(result.get("reason") or "kol not found"))
    return result
