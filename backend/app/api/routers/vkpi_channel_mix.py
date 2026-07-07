"""CB2 渠道组合器路由 —— 「用哪些渠道推、各占多少」跨渠道预算与权重分配。

- GET /api/admin/vkpi/channel/mix?sku=&budget_usd=&goal=
  → 五渠道预算与权重分配(KOL / 官号 / 独立站 / Dealer / 付费放大),按 Binet-Field 60/40
  分层(品牌层=KOL 情绪内容+官号,激活层=独立站+Dealer 促销+付费放大)。
  实现在 app.domains.channel.channel_mix(纯读规则聚合,零 LLM 零采集零写库;
  KOL 复用 strategy_sim 候选/预算逻辑,付费引用 growth_playbook 两段式放大规则)。

诚实态:SKU 不存在 404;预算非法 domain 层返回 status="invalid";
Dealer 本地 0 行 / 独立站本地无订单一律 status=data_missing + note,绝不编数;
聚合内部异常不 500,回 {status:"error", reason}(增益面板非阻塞)。
红线:纯读展示,零触 viltrox_fit_score、不碰 rule_v0;数字全带 basis 可追溯。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-channel-mix"])


@router.get("/channel/mix")
def channel_mix_route(
    sku: str = Query(..., min_length=1, max_length=120),
    budget_usd: float = Query(..., gt=0, le=10_000_000),
    goal: str | None = Query(default=None, max_length=30),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """跨渠道预算与权重分配(全只读,不写库;同输入同输出决定性)。"""
    del staff
    from app.domains.channel import channel_mix as channel_mix_domain

    try:
        return channel_mix_domain.channel_mix(sku, float(budget_usd), goal=goal)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 增益面板失败不炸接口,诚实回原因
        logger.warning("channel_mix failed for sku=%s budget=%s: %s", sku, budget_usd, exc)
        return {"status": "error", "reason": str(exc)[:300], "sku": str(sku)[:120]}
