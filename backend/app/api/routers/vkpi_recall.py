"""V-KPI 语义召回三段式路由。

- GET /api/admin/vkpi/recall/semantic?query=&limit=20
  → 三段式语义召回:①召回(OpenAI embedding × Qdrant 档案索引优先;不可用决定性
  降级 3-gram+词表,method 诚实标 ngram_fallback_v0)②final_v1 十一维余弦粗排
  ③LLM top-20 重排逐人「为何合适」(预算闸+当日缓存;预算不足 rerank=skipped)。
  实现在 app.domains.kol.recall_pipeline(payload 恒带 stages 三段追溯)。

诚实态:query 为空 400;零候选由 domain 层返回 {status:"empty", reason};
聚合内部异常不 500,回 {status:"error", reason}(前端安静缺席,增益块非阻塞),
error payload 也保 stages 键形状(全链可追溯契约不因异常缺角)。
红线:纯读展示(仅当日缓存写 vkpi_analysis_cache),零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-recall"])


@router.get("/recall/semantic")
def get_semantic_recall(
    query: str = "",
    limit: int = 20,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """语义召回三段式:embedding 优先召回 → 十一维粗排 → LLM top-20 重排(可降级、可追溯)。"""
    del staff
    from app.domains.kol import recall_pipeline

    text = str(query or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="query is required")
    try:
        return recall_pipeline.semantic_recall(text, limit=int(limit))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 增益块失败不炸接口,诚实回原因
        logger.warning("semantic_recall failed for query=%s: %s", text[:80], exc)
        return {
            "status": "error",
            "reason": str(exc)[:300],
            "items": [],
            "stages": {
                "recall": {"method": "none", "candidates": 0},
                "coarse": {"n": 0},
                "rerank": {"status": "skipped", "cost_note": "pipeline_error_zero_cost"},
            },
        }
