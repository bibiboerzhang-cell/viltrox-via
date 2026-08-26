"""MY KOL 一键数据关注 + 按产品聚合播放总览路由(波 D·B 车道,append-only)。

  POST /api/admin/vkpi/my-kol/{kol_pool_id}/videos/{evidence_id}/data-watch
  GET  /api/admin/vkpi/my-kol/sku-play-overview
  GET  /api/admin/vkpi/my-kol/sku-play-refresh/plan  报价:这次要去平台取几次数(纯 SELECT)
  POST /api/admin/vkpi/my-kol/sku-play-refresh       派活:唯一花钱的一步,必须带报价指纹

重新实测两端点(2026-08-25 补服务端硬闸):单次上限 / 每日上限 / 冷却一律**服务端**判,
口径照抄内容墙侧;绕开前端也拿不到无上限的批量取数。GET 零副作用已登记
release_validation 只读白名单,同族 POST 刻意不登记(它就该被发布围栏挡住)。
报价与派活之间用 plan_hash + expected_count 绑定:服务端重算比对,对不上一条都不派。

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
from app.domains.kol import (
    sku_play_overview,
    sku_play_refresh_dispatch,
    sku_play_refresh_plan,
    video_data_watch,
    video_tracking,
)


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


def _resolved_scope(staff: dict, staff_id: int | None) -> int | None:
    """报价 / 派活的可见范围:员工恒被压回本人,管理层缺省全团队。"""

    target = scope.effective_staff_id(staff, staff_id)
    if target is None and not scope.can_view_all(staff):
        raise HTTPException(status_code=403, detail="no staff identity in scope")
    return target


@router.get("/my-kol/sku-play-refresh/plan")
def my_kol_sku_play_refresh_plan_endpoint(
    sku_code: str = Query(..., min_length=1, max_length=120),
    evidence_id: int | None = Query(default=None, ge=1),
    staff_id: int | None = Query(default=None, ge=1),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """报价:这次要对几条视频重新取数、几条被闸挡下、今天还剩多少额度。

    纯 SELECT + 只读预算投影;不入队、不取数、不写库,可安全反复调用。
    """

    target = _resolved_scope(staff, staff_id)
    try:
        body = sku_play_refresh_plan.plan_sku_play_refresh(
            get_conn(),
            staff=staff,
            staff_scope_id=target,
            sku_code=str(sku_code),
            evidence_id=int(evidence_id or 0),
        )
    except Exception as exc:  # noqa: BLE001 — 不把 PG 原文透传给客户端
        logger.exception("my_kol.sku_play_refresh_plan_failed")
        raise HTTPException(status_code=500, detail="sku play refresh plan failed") from exc
    body["scope_context"] = scope.scope_context(staff, staff_id)
    return body


@router.post("/my-kol/sku-play-refresh", status_code=202)
@audit_action(
    action_type="my_kol_sku_play_refresh",
    target_type="kol_video_evidence_batch",
    target_id_extractor=lambda result, kwargs: str((kwargs.get("body") or {}).get("sku_code") or ""),
    detail_extractor=lambda result, kwargs: (
        "sku play refresh {status} planned={planned} queued={queued}".format(
            status=result.get("status", ""),
            planned=(result.get("counts") or {}).get("planned", 0),
            queued=(result.get("counts") or {}).get("queued", 0),
        )
        if isinstance(result, dict)
        else "sku play refresh"
    ),
    metadata_extractor=lambda result, kwargs: {
        "sku_code": (kwargs.get("body") or {}).get("sku_code"),
        "evidence_id": (kwargs.get("body") or {}).get("evidence_id"),
        "counts": result.get("counts") if isinstance(result, dict) else None,
    },
)
def my_kol_sku_play_refresh_endpoint(
    body: dict = Body(default_factory=dict),
    staff_id: int | None = Query(default=None, ge=1),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """按报价派活(唯一花钱的一步)。报价指纹对不上一条都不派。"""

    target = _resolved_scope(staff, staff_id)
    conn = get_conn()
    try:
        result = sku_play_refresh_dispatch.run_sku_play_refresh(
            conn,
            staff=staff,
            staff_scope_id=target,
            sku_code=str(body.get("sku_code") or ""),
            evidence_id=int(body.get("evidence_id") or 0),
            plan_hash=str(body.get("plan_hash") or ""),
            expected_count=body.get("expected_count"),
        )
        conn.commit()
    except sku_play_refresh_dispatch.SkuPlayRefreshError as exc:
        _rollback_quietly(conn)
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, **exc.detail},
        ) from exc
    except Exception:
        _rollback_quietly(conn)
        raise
    return result
