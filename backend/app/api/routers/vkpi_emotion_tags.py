"""V-KPI E1 情绪标签路由(P2G 2a 情绪体系,词表零成本)。

- GET  /api/admin/vkpi/kol-pool/{kol_pool_id}/emotion-profile
  → 该 KOL 已析视频的情绪分布聚合(两轴象限/器材四情绪/awe/hook_type/has_cart)。
  实现在 app.domains.kol.emotion_tags(纯读聚合已回打标签,零 LLM、零重析)。
- POST /api/admin/vkpi/emotion-tags/backfill?dry_run=&limit=&force=
  → 规则法回打器:对已析 final_v1 视频文本打词表标签,写 llm_dimensions_11 附加键
  emotion_tags_v1(不覆盖 LLM 原产物键;幂等复跑 0 新写)。dry_run 默认 True。

诚实态:KOL 不存在 404;未回打/无深析返回 {status:"empty", reason};聚合内部异常
不 500,回 {status:"error", reason}(增益块非阻塞)。
【成本红线】两端点均绝不触发 LLM 调用或视频重析;回打是纯词表(零成本)。
老红线:零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-emotion-tags"])


@router.get("/kol-pool/{kol_pool_id}/emotion-profile")
def get_kol_emotion_profile(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """KOL 情绪画像:已析视频的情绪标签分布(全只读,不写库,不触发分析)。"""
    del staff
    from app.domains.kol import emotion_tags

    try:
        return emotion_tags.video_emotion_profile(int(kol_pool_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 增益块失败不炸接口,诚实回原因
        logger.warning("video_emotion_profile failed for kol_pool_id=%s: %s", kol_pool_id, exc)
        return {"status": "error", "reason": str(exc)[:300], "kol_pool_id": int(kol_pool_id)}


@router.post("/emotion-tags/backfill")
def backfill_emotion_tags(
    dry_run: bool = Query(True, description="True=只统计不写库(默认);False=真写 emotion_tags_v1"),
    limit: int | None = Query(None, ge=1, le=10000, description="只处理前 N 条已析行(默认全量)"),
    force: bool = Query(False, description="True=同版本词表也重打(默认幂等跳过)"),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """词表回打器:零 LLM、零重析、零采集;写入仅为 jsonb 单键附加,幂等。"""
    del staff
    from app.domains.kol import emotion_tags

    try:
        return emotion_tags.tag_analyzed_videos(dry_run=bool(dry_run), limit=limit, force=bool(force))
    except Exception as exc:  # noqa: BLE001 — 回打失败诚实回原因,不留半截事务
        logger.warning("tag_analyzed_videos failed (dry_run=%s): %s", dry_run, exc)
        return {"status": "error", "reason": str(exc)[:300], "dry_run": bool(dry_run)}
