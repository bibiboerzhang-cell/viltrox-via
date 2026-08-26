"""内容墙「去查最新内容」三端点(append-only 新文件;vkpi_my_kol.py 已 987 行不可再塞)。

  GET  /api/admin/vkpi/my-kol/wall-fetch/plan    报价:这次要去几个账号取内容(纯 SELECT)
  POST /api/admin/vkpi/my-kol/wall-fetch         派活:唯一花钱的一步,必须带报价指纹
  GET  /api/admin/vkpi/my-kol/wall-fetch/status  回读:派出去的活现在到哪一步(纯 SELECT)

两个 GET 零副作用、零入队、零 provider,可安全反复调用——都已登记 release_validation
只读白名单。POST 刻意**不**登记白名单:它就该被发布围栏挡住。

status 是 2026-08-24 线上 P0 的解药:派出去的活被拦死,界面却永远停在「已安排,还没
结果回来」。它复用 apify_jobs 的任务态投影(与顶栏进度中心同一张表、同一套状态口径),
不新造第二套进度真源,也不做长连接——由前端**有限次**回读,读不到就如实说读不到。

报价与派活之间用 ``plan_hash`` + ``expected_count`` 绑定:服务端在 POST 里重算一遍报价
再比对,对不上直接 409 让操作员重看。没有这道,「确认框写 3 个、实际派 30 个」是完全
可能发生的,而且是最难被发现的撒谎形态。

路由段形状:``/my-kol/wall-fetch`` 与 ``/my-kol/wall-fetch/plan`` 都是字面段,
与 vkpi_my_kol 的 ``/my-kol/{kol_pool_id}/...`` 三段家族不冲突。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.access import scope
from app.domains.audit.decorator import audit_action
from app.domains.kol import my_kol_wall_fetch, my_kol_wall_fetch_plan


router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-my-kol-wall-fetch"])
logger = get_logger(__name__)


def _resolved_scope(staff: dict, staff_id: int | None) -> int | None:
    target = scope.effective_staff_id(staff, staff_id)
    if target is None and not scope.can_view_all(staff):
        raise HTTPException(status_code=403, detail="no staff identity in scope")
    return target


def _rollback_quietly(conn: Any) -> None:
    try:
        conn.rollback()
    except Exception as exc:  # noqa: BLE001 — 回滚失败只记类型,不掩盖原始错误
        logger.warning("wall fetch rollback failed: %s", type(exc).__name__)


@router.get("/my-kol/wall-fetch/plan")
def my_kol_wall_fetch_plan_endpoint(
    days: int = Query(default=0, ge=0, le=365),
    kol_pool_id: int | None = Query(default=None, ge=1),
    staff_id: int | None = Query(default=None, ge=1),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """报价:这次要去几个账号取内容、几个刚取过会跳过、本月额度还剩几成。

    纯 SELECT + 只读预算投影;不入队、不调 provider、不写库。
    """

    target = _resolved_scope(staff, staff_id)
    try:
        body = my_kol_wall_fetch_plan.plan_wall_fetch(
            get_conn(),
            staff=staff,
            staff_scope_id=target,
            kol_pool_id=int(kol_pool_id or 0),
            days=int(days),
        )
    except Exception as exc:  # noqa: BLE001 — 不把 PG 原文透传给客户端
        logger.exception("my_kol.wall_fetch_plan_failed")
        raise HTTPException(status_code=500, detail="wall fetch plan failed") from exc
    body["scope_context"] = scope.scope_context(staff, staff_id)
    return body


@router.get("/my-kol/wall-fetch/status")
def my_kol_wall_fetch_status_endpoint(
    job_ids: str = Query(default="", max_length=512),
    staff_id: int | None = Query(default=None, ge=1),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """回读派出去的活:还在等 / 已经取回来 / 没能取到(附一句人话原因)。

    纯 SELECT;只认本车道自己派出的记录,非管理层再叠一层「只看自己派的」。
    读不到的一律进 ``unknown_job_ids``——「读不到」绝不当成「已完成」。
    """

    target = _resolved_scope(staff, staff_id)
    parsed: list[int] = []
    for chunk in str(job_ids or "").split(",")[: my_kol_wall_fetch.OUTCOME_LOOKUP_LIMIT]:
        text = chunk.strip()
        if text.isdigit():
            parsed.append(int(text))
    try:
        return my_kol_wall_fetch.read_dispatch_outcomes(
            get_conn(),
            job_ids=parsed,
            staff_scope_id=target,
            scoped=not scope.can_view_all(staff),
        )
    except Exception as exc:  # noqa: BLE001 — 不把 PG 原文透传给客户端
        logger.exception("my_kol.wall_fetch_status_failed")
        raise HTTPException(status_code=500, detail="wall fetch status failed") from exc


@router.post("/my-kol/wall-fetch", status_code=202)
@audit_action(
    action_type="my_kol_wall_fetch",
    target_type="kol_pool_collection",
    target_id_extractor=lambda result, kwargs: str((kwargs.get("body") or {}).get("kol_pool_id") or "all"),
    detail_extractor=lambda result, kwargs: (
        "wall fetch {status} planned={planned} queued={queued}".format(
            status=result.get("status", ""),
            planned=(result.get("counts") or {}).get("planned", 0),
            queued=(result.get("counts") or {}).get("queued", 0),
        )
        if isinstance(result, dict)
        else "wall fetch"
    ),
    metadata_extractor=lambda result, kwargs: {
        "days": (kwargs.get("body") or {}).get("days"),
        "kol_pool_id": (kwargs.get("body") or {}).get("kol_pool_id"),
        "counts": result.get("counts") if isinstance(result, dict) else None,
    },
)
def my_kol_wall_fetch_endpoint(
    body: dict = Body(default_factory=dict),
    staff_id: int | None = Query(default=None, ge=1),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """按报价派活(唯一花钱的一步)。报价指纹对不上一条都不派。"""

    target = _resolved_scope(staff, staff_id)
    conn = get_conn()
    try:
        result = my_kol_wall_fetch.run_wall_fetch(
            conn,
            staff=staff,
            staff_scope_id=target,
            kol_pool_id=int(body.get("kol_pool_id") or 0),
            days=int(body.get("days") or 0),
            plan_hash=str(body.get("plan_hash") or ""),
            expected_count=body.get("expected_count"),
        )
    except my_kol_wall_fetch.WallFetchError as exc:
        _rollback_quietly(conn)
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, **exc.detail},
        ) from exc
    except Exception:
        _rollback_quietly(conn)
        raise
    return result
