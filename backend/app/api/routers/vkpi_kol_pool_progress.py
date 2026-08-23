"""backend/app/api/routers/vkpi_kol_pool_progress.py

账号级视频深析进度(只读)端点簇;主 router(prefix=/api/admin/vkpi)include,路径逐字同族。

GET /kol-pool/{kol_pool_id}/video-analysis-progress
    → domains.kol.video_analysis_enqueue.account_video_analysis_progress 原样返回(O→F 契约):
      items[*].failure_category / failure_reason_human / failure_code,顶层 eta_seconds 与 eta.*。
    行级门禁与 /my-kol/{id}/videos 同款 assert_target_readable;零写、零 LLM、零 fit。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.kol import video_analysis_enqueue as kol_video_analysis_enqueue
from app.domains.kol.my_kol_paid_action_access import MyKolPaidActionError, assert_target_readable
from app.domains.kol.video_analysis_progress_reasons import FAILURE_CATEGORIES

router = APIRouter(tags=["vkpi-kol-pool"])
logger = get_logger(__name__)

PROGRESS_CONTRACT = "account_video_analysis_progress_v2"


@router.get("/kol-pool/{kol_pool_id}/video-analysis-progress")
def get_pool_item_video_analysis_progress(
    kol_pool_id: int,
    limit: int = Query(default=kol_video_analysis_enqueue.KOL_DEEP_ANALYSIS_VIDEO_LIMIT, ge=1, le=200),
    include_items: bool = Query(default=True),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """账号 N 条视频 final_v1 整体进度:完成/进行中/失败(带可读原因)/ETA(按活跃车道)。"""
    pid = int(kol_pool_id or 0)
    if pid <= 0:
        raise HTTPException(status_code=400, detail="kol_pool_id required")
    conn = get_conn()
    try:
        assert_target_readable(conn, kol_pool_id=pid, staff=staff)
    except MyKolPaidActionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
    try:
        result = kol_video_analysis_enqueue.account_video_analysis_progress(
            conn,
            pid,
            limit=int(limit),
            include_items=bool(include_items),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="video_analysis_progress_invalid_request") from exc
    except Exception as exc:  # noqa: BLE001 - 进度只读端点不 500 裸炸,诚实回原因类
        logger.warning("vkpi.video_analysis_progress_read_failed | kol_pool_id=%s", pid, exc_info=True)
        raise HTTPException(
            status_code=503,
            detail={"code": "video_analysis_progress_unavailable", "message": "进度暂时不可读,请稍后重试。", "retryable": True},
        ) from exc
    return {
        **result,
        "contract": PROGRESS_CONTRACT,
        "failure_categories": list(FAILURE_CATEGORIES),
        "read_only": True,
    }


__all__ = ["PROGRESS_CONTRACT", "router"]
