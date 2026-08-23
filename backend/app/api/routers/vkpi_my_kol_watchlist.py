"""MY KOL 观察清单路由(波 C·C5,纯读,append-only)。

  GET /api/admin/vkpi/my-kol/watch-overview                   本人全部分组各一张汇总卡
  GET /api/admin/vkpi/my-kol/groups/{group_id}/watch-overview 组内每个 KOL 一行进度

分组 = 既有 vkpi_staff_groups(组级共享 KOL permissions.shared_kol_pool_ids),不造新表。
权限:require_tab("vkpi","read") + 只能看本人分组(管理层全量;口径见
domains/kol/watchlist_overview.group_visible_to)。零 provider / 零 LLM / 零写入。
路由顺序:/my-kol/watch-overview 两段、/my-kol/groups/{id}/watch-overview 末段固定,
与 vkpi_my_kol 的 /my-kol/{kol_pool_id}/... 不冲突。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.access import scope
from app.domains.kol import watchlist_overview


router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-my-kol-watchlist"])
logger = get_logger(__name__)


def _require_identity(staff: dict) -> None:
    if scope.actor_staff_id(staff) <= 0 and not scope.can_view_all(staff):
        raise HTTPException(status_code=403, detail="no staff identity in scope")


@router.get("/my-kol/watch-overview")
def my_kol_watch_overview_endpoint(
    group_limit: int = Query(default=watchlist_overview.MAX_GROUPS, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """本人全部分组的观察清单汇总(每组一张卡,不展开 KOL 行;纯 SELECT)。"""

    _require_identity(staff)
    try:
        return watchlist_overview.watch_overview(get_conn(), staff=staff, group_limit=int(group_limit))
    except Exception as exc:  # noqa: BLE001 — 不把 PG 原文透传给客户端
        logger.exception("my_kol.watch_overview_failed")
        raise HTTPException(status_code=500, detail="watch overview failed") from exc


@router.get("/my-kol/groups/{group_id}/watch-overview")
def my_kol_group_watch_overview_endpoint(
    group_id: str,
    kol_limit: int = Query(default=watchlist_overview.MAX_KOLS_PER_GROUP, ge=1, le=watchlist_overview.MAX_KOLS_PER_GROUP),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """单个分组的观察清单:组内每个 KOL 一行(追踪/快照/7d 增量/深析/失败/镜头;纯 SELECT)。"""

    _require_identity(staff)
    gid = str(group_id or "").strip()
    if not gid:
        raise HTTPException(status_code=400, detail="group_id required")
    try:
        return watchlist_overview.group_overview(get_conn(), group_id=gid, staff=staff, kol_limit=int(kol_limit))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="group not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="not a member of this group") from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("my_kol.group_watch_overview_failed | group_id=%s", gid)
        raise HTTPException(status_code=500, detail="watch overview failed") from exc
