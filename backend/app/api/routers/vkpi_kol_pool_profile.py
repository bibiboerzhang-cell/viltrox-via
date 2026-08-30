"""backend/app/api/routers/vkpi_kol_pool_profile.py

行为不变迁出:画像/候选子域端点簇(关注自动轮询状态 / 数字孪生 / 项目可选名单)。
原 vkpi_kol_pool.py 通过 router.include_router(_kol_pool_profile_router) 兜住;
本子 router 无 prefix,include 后继承父 router 的 /api/admin/vkpi,路径逐字不变。

铁律:本文件端点的先后顺序 = 拆分前父文件里的注册顺序,逐条照抄,绝不重排
(路由表顺序 = 对外行为;test_router_package_lazy_import_contract 钉了全表 sha)。
include 点也钉死在父文件 enrich-via-apify 之后 / jobs 子 router include 之前的原位。
/kol-pool/available 是单段静态 GET,必须先于 /kol-pool/{kol_pool_id} 注册,
否则被当 int 解析 → 永久 422。

红线:零触 viltrox_fit_score;本模块全部为只读路径。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.domains.projects import workflow as project_workflow


router = APIRouter(tags=["vkpi-kol-pool"])


@router.get("/kol-pool/auto-poll/status")
def kol_pool_auto_poll_status(staff=Depends(require_tab("vkpi", "read"))) -> dict:
    """D3 · 关注 KOL 自动轮询状态:应轮询候选数 + 队列可用性(只读,全容错)。"""
    from app.domains.kol import auto_poll

    return auto_poll.auto_poll_status()


@router.get("/kol-pool/{kol_pool_id}/twin")
def kol_twin(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """C3 · KOL 数字孪生:合作判断档案(身份+为什么记住+数据等级+历史表现+学习信号+合作建议)。"""
    from app.domains.kol import twin

    return twin.get_kol_twin(int(kol_pool_id), staff=staff)


@router.get("/kol-pool/available")
def list_available_for_project(
    project_id: int = Query(..., ge=1),
    query: str = Query(default=""),
    limit: int = Query(default=200, ge=1, le=500),
    scope: str = Query(default="favorites"),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """KOL Pool candidates not yet assigned to the project.

    scope=favorites(默认)只返本人收藏子集;scope=all 显式逃生门返全池(诊断 P0-2 裁决)。
    """
    try:
        return project_workflow.list_available_project_kols(
            project_id,
            query=query,
            limit=limit,
            scope_mode=scope,
            staff=staff,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        if exc.__class__.__name__ == "ScopeDenied":
            raise HTTPException(status_code=403, detail=str(exc) or "scope denied") from exc
        raise
