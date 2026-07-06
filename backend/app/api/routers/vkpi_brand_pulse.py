"""V-KPI 品牌脉搏路由(Brand Pulse)。

- GET /api/admin/vkpi/market/brand-pulse?window_days=90
  → 全池品牌露出的周度趋势:rising/falling/stable 三组 + Viltrox 自身曲线与相对位置
  (rank / share_of_voice)+ 每品牌 top 例证视频。
  实现在 app.domains.market.brand_pulse(纯读端聚合已有数据,零新采集、零 LLM、零写库)。

诚实态:窗口内没数据回 status="no_data_in_window",有数据但词表零命中回
"no_brand_signal";聚合内部异常不 500,回 {status:"error", reason}(前端安静缺席)。
红线:纯读展示,零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-brand-pulse"])


@router.get("/market/brand-pulse")
def get_market_brand_pulse(
    window_days: int = 90,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """品牌脉搏:全池品牌声量周度趋势 + Viltrox 相对位置(全只读,不写库)。"""
    del staff
    from app.domains.market import brand_pulse

    try:
        return brand_pulse.get_brand_pulse(window_days=window_days)
    except Exception as exc:  # noqa: BLE001 — 增益块失败不炸接口,诚实回原因
        logger.warning("brand_pulse failed for window_days=%s: %s", window_days, exc)
        return {"status": "error", "reason": str(exc)[:300], "window_days": int(window_days or 0)}
