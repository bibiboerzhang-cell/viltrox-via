"""V-KPI 行业对照路由(Industry Benchmark)。

- GET /api/admin/vkpi/strategy/industry-benchmark?window_days=90
  → 「Viltrox 在行业里站在哪」竞品全景对照:每品牌声量(提及视频/独立 KOL/总播放/均播放)
  + 声量份额排名 + 环比动量 + 内容质量侧写(均互动率)+ 焦段覆盖格局(sku_weak/voice_weak 格子)
  + head_to_head 三行对比与规则模板一句话差距。
  实现在 app.domains.market.industry_benchmark(纯读端聚合已有数据,零新采集、零 LLM、零写库)。

诚实态:窗口内没数据回 status="no_data_in_window",词表零命中回 "no_brand_signal";
聚合内部异常不 500,回 {status:"error", reason}(前端安静缺席)。
红线:纯读展示,零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-industry-benchmark"])


@router.get("/strategy/industry-benchmark")
def get_industry_benchmark(
    window_days: int = 90,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """行业对照全景:品牌声量/份额/动量/质量/焦段格局 + head_to_head(全只读,不写库)。"""
    del staff
    from app.domains.market import industry_benchmark

    try:
        return industry_benchmark.benchmark(window_days=window_days)
    except Exception as exc:  # noqa: BLE001 — 增益块失败不炸接口,诚实回原因
        logger.warning("industry_benchmark failed for window_days=%s: %s", window_days, exc)
        return {"status": "error", "reason": str(exc)[:300], "window_days": int(window_days or 0)}
