"""Provider-free KOL recall preview route."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger
import app.domains.kol.profile_recall as kol_profile_recall


router = APIRouter(tags=["vkpi-kol-pool"])
logger = get_logger(__name__)


def _service_unavailable(reason: str, operation: str) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "status": "unavailable",
            "reason": reason,
            "operation": operation,
            "retryable": True,
        },
    )


@router.get("/kol-recall")
def recall_kol_profiles(
    query_text: str = Query(default="", max_length=256),
    product_sku: str = Query(default="", max_length=256),
    candidate_limit: int = Query(default=50, ge=1, le=500),
    limit: int = Query(default=10, ge=1, le=50),
    creator_quota: int = Query(default=7, ge=0, le=50),
    reviewer_quota: int = Query(default=3, ge=0, le=50),
    ratio_policy: str = Query(default="soft"),
    mixed_policy: str = Query(default="dominant"),
    dedupe: bool = Query(default=True),
    vector_weight: float = Query(default=0.85, ge=0, le=1),
    type_weight: float = Query(default=0.15, ge=0, le=1),
    type_boost_enabled: bool = Query(default=True),
    exclude_chinese: bool = Query(default=True),
    session_id: int | None = Query(default=None, ge=1),
    create_session: bool = Query(default=False),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Provider-free KOL preview; all paid/deep recall stays in POST workers."""
    try:
        if create_session or session_id is not None:
            raise HTTPException(
                status_code=400,
                detail="GET /kol-recall is read-only; use POST /kol-smart-search to create or update a search session",
            )
        result = kol_profile_recall.recall_kol_profiles(
            query_text=query_text,
            product_sku=product_sku,
            candidate_limit=candidate_limit,
            limit=limit,
            creator_quota=creator_quota,
            reviewer_quota=reviewer_quota,
            ratio_policy=ratio_policy,
            mixed_policy=mixed_policy,
            dedupe=dedupe,
            vector_weight=vector_weight,
            type_weight=type_weight,
            type_boost_enabled=type_boost_enabled,
            exclude_chinese=exclude_chinese,
            provider_free=True,
            operator_query_text=query_text,
        )
        result["provider_calls"] = False
        result["write_db"] = False
        result["execution_mode"] = "provider_free_preview"
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise _service_unavailable("kol_recall_unavailable", "kol_recall") from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("kol_recall failed query=%s", query_text[:160])
        raise HTTPException(
            status_code=503,
            detail="KOL 召回服务暂时不可用，当前结果未被标记为完成；请稍后重试。",
        ) from exc
