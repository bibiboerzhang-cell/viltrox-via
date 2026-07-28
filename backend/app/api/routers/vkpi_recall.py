"""V-KPI 语义召回三段式路由。

- GET /api/admin/vkpi/recall/semantic?query=&limit=20
  → Provider-free 预览:①本地 3-gram+词表召回 ②final_v1 十一维余弦粗排
  ③明确跳过付费 LLM 重排。深度 embedding/LLM 管线仍保留给受控后台任务，
  GET 不调用 Provider、不写当日缓存。payload 恒带 stages 三段追溯。

诚实态:query 为空 400;零候选由 domain 层返回 {status:"empty", reason};
聚合内部异常不 500,回 {status:"error", reason}(前端安静缺席,增益块非阻塞),
error payload 也保 stages 键形状(全链可追溯契约不因异常缺角)。
红线:GET 纯读、零 Provider、零缓存写，且零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-recall"])


@router.get("/recall/semantic")
def get_semantic_recall(
    query: str = Query(default="", max_length=256),
    limit: int = Query(default=20, ge=1, le=50),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Provider-free 召回预览:本地召回 → 十一维粗排，跳过付费重排。"""
    del staff
    from app.domains.kol import recall_pipeline

    text = str(query or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="query is required")
    try:
        result = recall_pipeline.semantic_recall(
            text,
            limit=int(limit),
            provider_free=True,
        )
        result["provider_calls"] = False
        result["write_db"] = False
        result["execution_mode"] = "provider_free_preview"
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 增益块失败不炸接口,诚实回原因
        logger.warning("semantic_recall failed for query=%s", text[:80], exc_info=True)
        return {
            "status": "error",
            "reason": "semantic_recall_unavailable",
            "items": [],
            "stages": {
                "recall": {"method": "none", "candidates": 0},
                "coarse": {"n": 0},
                "rerank": {"status": "skipped", "cost_note": "pipeline_error_zero_cost"},
            },
        }
