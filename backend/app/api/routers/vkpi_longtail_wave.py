"""G3 长尾波次一键铺量路由 —— 「给这支 SKU 铺 50-100 个 nano/micro」dry_run 预览。

- GET /api/admin/vkpi/launch/longtail-wave?sku=&count=&budget_usd=
  → 长尾名单(报价升序,与 strategy_sim longtail_spread 同源同键)+ 每人小额预算
  (rate_card)+ 建议动作包(送样 + 佣金码「待生成」占位)+ 可喂 action inbox 的
  批量建议预览。实现在 app.domains.projects.longtail_wave(零写库零外联零 LLM)。

诚实态:SKU 不存在 404;长尾候选不足由 domain 层返回 status="empty"+reason;
聚合内部异常不 500,回 {status:"error", reason}(增益面板非阻塞)。
红线:dry_run 恒 True 绝不落库;绝不真发外联/真生成佣金码(goaffpro 留给执行层);
零触 viltrox_fit_score、不碰 rule_v0;数字全带 basis 可追溯。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-longtail-wave"])


@router.get("/launch/longtail-wave")
def get_longtail_wave(
    sku: str = Query(..., min_length=1, max_length=120),
    count: int = Query(default=50, ge=5, le=100),
    budget_usd: float | None = Query(default=None, gt=0, le=10_000_000),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """长尾波次 dry_run 预览(全只读,不写库;同输入同输出决定性)。"""
    del staff
    from app.domains.projects import longtail_wave

    try:
        return longtail_wave.longtail_wave(sku, count=int(count), budget_usd=budget_usd)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 增益面板失败不炸接口,诚实回原因
        logger.warning("longtail_wave failed for sku=%s count=%s: %s", sku, count, exc)
        return {"status": "error", "reason": str(exc)[:300], "sku": str(sku)[:120]}
