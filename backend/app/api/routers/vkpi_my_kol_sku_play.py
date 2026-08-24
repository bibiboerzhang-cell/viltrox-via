"""MY KOL 一键数据关注 + 按产品聚合播放总览路由(波 D·B 车道,append-only)。

  POST /api/admin/vkpi/my-kol/{kol_pool_id}/videos/{evidence_id}/data-watch
  GET  /api/admin/vkpi/my-kol/sku-play-overview

POST 是唯一写口:SKU 解析在 domains/kol/video_data_watch,落地 100% 复用
video_tracking 既有行级围栏 / SKU 校验 / 幂等入队(零 provider);解析不出
SKU 时 200 + status=sku_required + 候选清单,由用户点选拍板。
GET 纯读,收藏集口径(收藏 ∪ 授权共享):员工恒看本人,管理层可全团队。
路由顺序:/my-kol/sku-play-overview 两段路径,与 vkpi_my_kol 的
/my-kol/{kol_pool_id}/... 三段家族不冲突;data-watch 五段字面尾不冲突。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.access import scope
from app.domains.audit.decorator import audit_action
from app.domains.kol import sku_play_overview, video_data_watch, video_tracking


router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-my-kol-sku-play"])
logger = get_logger(__name__)


def _require_identity(staff: dict) -> None:
    if scope.actor_staff_id(staff) <= 0 and not scope.can_view_all(staff):
        raise HTTPException(status_code=403, detail="no staff identity in scope")


def _rollback_quietly(conn: Any) -> None:
    try:
        conn.rollback()
    except Exception as exc:
        logger.warning("MY KOL data watch rollback failed: %s", type(exc).__name__)


@router.post("/my-kol/{kol_pool_id}/videos/{evidence_id}/data-watch")
@audit_action(
    action_type="my_kol_video_data_watch",
    target_type="kol_video_evidence",
    target_id_extractor=lambda result, kwargs: str(kwargs.get("evidence_id") or ""),
    detail_extractor=lambda result, kwargs: (
        "data watch {status} skus={skus}".format(
            status=result.get("status", ""),
            skus=",".join(result.get("skus") or []),
        )
        if isinstance(result, dict)
        else "data watch"
    ),
    metadata_extractor=lambda result, kwargs: {
        "status": result.get("status", "") if isinstance(result, dict) else "",
        "sku_source": result.get("sku_source", "") if isinstance(result, dict) else "",
        "refresh": result.get("refresh", "") if isinstance(result, dict) else "",
        "kol_pool_id": kwargs.get("kol_pool_id"),
    },
)
def my_kol_video_data_watch_endpoint(
    kol_pool_id: int,
    evidence_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """一键数据关注:解析 SKU → 复用既有追踪路径排队;解析不出 200+sku_required。"""

    conn = get_conn()
    try:
        result = video_data_watch.data_watch(
            conn,
            kol_pool_id=int(kol_pool_id),
            evidence_id=int(evidence_id),
            staff=staff,
            product_skus=body.get("product_skus"),
            confirm_detected_skus=body.get("confirm_detected_skus"),
        )
        conn.commit()
    except video_tracking.VideoTrackingError as exc:
        _rollback_quietly(conn)
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
    except LookupError as exc:
        _rollback_quietly(conn)
        raise HTTPException(status_code=404, detail="video_evidence_not_found") from exc
    except Exception:
        _rollback_quietly(conn)
        raise
    return result


@router.get("/my-kol/sku-play-overview")
def my_kol_sku_play_overview_endpoint(
    staff_id: int | None = Query(default=None, ge=1),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """按 SKU 聚合的被追踪视频播放总览(纯 SELECT;未实测一律 null,不编 0)。"""

    _require_identity(staff)
    try:
        body = sku_play_overview.build_sku_play_overview(
            get_conn(), staff=staff, staff_id=staff_id
        )
    except Exception as exc:  # noqa: BLE001 — 不把 PG 原文透传给客户端
        logger.exception("my_kol.sku_play_overview_failed")
        raise HTTPException(status_code=500, detail="sku play overview failed") from exc
    body["viewer_scope"] = scope.scope_context(staff, staff_id)
    return body
