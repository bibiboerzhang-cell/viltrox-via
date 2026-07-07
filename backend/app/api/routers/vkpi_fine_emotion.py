"""V-KPI 评论细粒度情绪 + 渴望密度路由(GTM-2 E3,模板抄 vkpi_signature.py)。

- GET  /api/admin/vkpi/kol-pool/{kol_pool_id}/desire-density
    → 渴望密度 KPI(渴望类评论/千条,带样本数+置信;样本<20 标 low)。纯读零写库。
- POST /api/admin/vkpi/comments/fine-emotion/backfill?dry_run=
    → 六类细粒度情绪词表回填(fine_emotion_v1 附加键,零 LLM 零成本;
      dry_run=true 默认只算分布不落库;幂等,二跑 written=0)。
- GET  /api/admin/vkpi/learning/desire-vs-conversion
    → 先导对账钩:渴望密度 × 短链/订单。本地数据近空 → 诚实 pending。

实现在 app.domains.comments.fine_emotion(纯词表规则,消费 growth_playbook)。
诚实态:KOL 不存在 404;聚合内部异常不 500,回 {status:"error", reason}。
红线:零 LLM、零视频重析、零采集;不触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-fine-emotion"])


@router.get("/kol-pool/{kol_pool_id}/desire-density")
def get_desire_density(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """渴望密度 KPI:渴望类评论/千条(纯读,零写库,零 LLM)。"""
    del staff
    from app.domains.comments import fine_emotion

    try:
        return fine_emotion.desire_density(kol_pool_id=int(kol_pool_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 增益块失败不炸接口,诚实回原因
        logger.warning("desire_density failed for kol_pool_id=%s: %s", kol_pool_id, exc)
        return {"status": "error", "reason": str(exc)[:300], "kol_pool_id": int(kol_pool_id)}


@router.post("/comments/fine-emotion/backfill")
def post_fine_emotion_backfill(
    dry_run: bool = True,
    account_id: int | None = None,
    limit: int = 0,
    force: bool = False,
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """六类细粒度情绪词表回填(纯词表零成本;dry_run 默认 true 只算不写)。"""
    del staff
    from app.domains.comments import fine_emotion

    try:
        return fine_emotion.classify_comments(
            dry_run=bool(dry_run),
            account_id=int(account_id) if account_id is not None else None,
            limit=int(limit or 0),
            force=bool(force),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("fine_emotion backfill failed: %s", exc)
        return {"status": "error", "reason": str(exc)[:300]}


@router.get("/learning/desire-vs-conversion")
def get_desire_vs_conversion(
    kol_pool_id: int | None = None,
    sku: str | None = None,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """先导对账钩:渴望密度 × 短链/订单(本地数据近空 → 诚实 pending)。"""
    del staff
    from app.domains.comments import fine_emotion

    try:
        return fine_emotion.desire_vs_conversion(
            kol_pool_id=int(kol_pool_id) if kol_pool_id is not None else None,
            sku=str(sku) if sku else None,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning("desire_vs_conversion failed: %s", exc)
        return {"status": "error", "reason": str(exc)[:300]}
