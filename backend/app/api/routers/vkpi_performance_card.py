"""G2 · KOL 战绩卡路由(件②)。

- GET /api/admin/vkpi/kol-pool/{kol_pool_id}/performance-card
  → 该 KOL 为 Viltrox 带来的战绩汇总:带品视频/播放/互动 + 商业转化(goaffpro/Shopify/短链
  守卫读,无数据诚实 0)+ 合作时间线 + 双语 share_text(模板法零 LLM,数字全真)。
  实现在 app.domains.kol.performance_card(纯聚合已有数据,零新采集、零写库)。

诚实态:KOL 不存在 404;各块缺数据由 domain 层返回 empty/available=False + reason;
聚合内部异常不 500,回 {status:"error", reason}(前端安静缺席,增益块非阻塞)。
红线:纯读展示,share_text 零内部评分/内部字段;零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-performance-card"])


@router.get("/kol-pool/{kol_pool_id}/performance-card")
def get_kol_performance_card(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """KOL 战绩卡:结构化 JSON + 可回传 KOL 的双语感谢短文(全只读,不写库)。"""
    from app.domains.kol import performance_card

    try:
        return performance_card.performance_card(int(kol_pool_id), staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 增益块失败不炸接口,诚实回原因
        logger.warning("performance_card failed for kol_pool_id=%s: %s", kol_pool_id, exc)
        return {"status": "error", "reason": str(exc)[:300], "kol_pool_id": int(kol_pool_id)}
