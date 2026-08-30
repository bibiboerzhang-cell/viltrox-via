"""backend/app/api/routers/vkpi_kol_pool_item.py

行为不变迁出:单项检索/档案子域端点簇(handle 解析 / 单项读 / 详情抽屉 bundle /
账号 dossier 读 / dossier 本地物化入队)。
原 vkpi_kol_pool.py 通过 router.include_router(_kol_pool_item_router) 兜住;
本子 router 无 prefix,include 后继承父 router 的 /api/admin/vkpi,路径逐字不变。
get_item / get_item_detail_bundle 在父文件 re-export 兜住既有测试的直呼调用点。

铁律:本文件端点的先后顺序 = 拆分前父文件里的注册顺序,逐条照抄,绝不重排
(路由表顺序 = 对外行为;test_router_package_lazy_import_contract 钉了全表 sha)。
include 点也钉死在父文件 needs-analysis 之后 / {kol_pool_id}/refresh 之前的原位。
/kol-pool/resolve 是单段静态 GET,必须先于本文件的 /kol-pool/{kol_pool_id} 注册,
否则被当 int 解析 → 永久 422(与 needs-analysis 同款吞路由陷阱)。

红线:零触 viltrox_fit_score;批量面永不吐明文联系方式(CONTACT_VISIBILITY_MASKED)。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response

from app.api.dependencies.perms import require_tab
from app.api.routers.vkpi_kol_contact_projection import PRIVATE_CONTACT_HEADERS
from app.domains.kol import account_dossier as kol_account_dossier
from app.domains.kol import account_dossier_extract as kol_account_dossier_extract
from app.domains.kol import history_match as kol_history_match
from app.domains.kol import pool as kol_pool
from app.domains.kol.pool_common import CONTACT_VISIBILITY_MASKED


router = APIRouter(tags=["vkpi-kol-pool"])


@router.get("/kol-pool/resolve")
def resolve_kol_pool(
    handle: str = Query(default=""),
    platform: str = Query(default=""),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """#17 按 handle(可选 platform)解析到 vkpi 主池记录。

    供 mover 预览弹窗(#5)/ KOLDetailModal 真指标(#22):用 handle 拿真 kol_pool_id +
    真 followers/avg_views/合作摘要。命中返回 history_match 全量 payload;未命中诚实
    返回 matched=False(前端据此走「先入库」或显空,不再编造假指标)。

    注册在 /{kol_pool_id} 动态路由之前:FastAPI 按声明顺序匹配,静态 /resolve 若排在
    /{kol_pool_id} 之后会被当 int 解析 → 永久 422(与 needs-analysis 同款吞路由陷阱)。
    """
    h = (handle or "").strip()
    plat = (platform or "").strip()
    if not h:
        return {"matched": False, "reason": "handle required"}
    item = {"handle": h, "display_name": h, "platform": plat}
    payload = kol_history_match.find_history_match(item, platform=plat)
    if not payload:
        return {"matched": False, "handle": h, "platform": plat}
    return payload


@router.get("/kol-pool/{kol_pool_id}")
async def get_item(
    request: Request,
    response: Response,
    kol_pool_id: int,
    refresh_if_stale: bool = Query(default=True),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """获取单个 KOL Pool 项；GET 不写搜索标记、不排队，刷新仅走显式 POST。"""
    del request, refresh_if_stale, staff
    response.headers.update(PRIVATE_CONTACT_HEADERS)
    try:
        result = kol_pool.get_item(
            int(kol_pool_id),
            contact_visibility=CONTACT_VISIBILITY_MASKED,
        )
        result["contact_projection_reason"] = "summary_only"
        return result
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="kol pool item not found", headers=PRIVATE_CONTACT_HEADERS) from exc


@router.get("/kol-pool/{kol_pool_id}/detail-bundle")
def get_item_detail_bundle(
    request: Request,
    response: Response,
    kol_pool_id: int,
    # P9:此前 default=3/max=10 把账号详情抽屉钉死在"前 4 条";底层 evidence 早已物化全量,
    # 抬到 default=24/max=200,让单账号详情默认展示该账号(基本)全部视频,前端可按需再加载。
    # 这是 READ-ONLY 物化展示口径(便宜),不触发新的 Gemini 深析(那是另一条限量+预算闸的链)。
    video_limit: int = Query(default=24, ge=1, le=200),
    llm_limit: int = Query(default=20, ge=1, le=50),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Read-only detail drawer bundle; does not refresh providers or touch V6 Fit."""
    response.headers.update(PRIVATE_CONTACT_HEADERS)
    try:
        result = kol_pool.detail_bundle(
            int(kol_pool_id),
            video_limit=video_limit,
            llm_limit=llm_limit,
            contact_visibility=CONTACT_VISIBILITY_MASKED,
        )
        result["contact_projection_reason"] = "summary_only"
        return result
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="kol pool item not found", headers=PRIVATE_CONTACT_HEADERS) from exc


@router.get("/kol-pool/{kol_pool_id}/account-dossier")
def get_pool_item_account_dossier(
    kol_pool_id: int,
    video_limit: int = Query(default=50, ge=1, le=200),
    event_limit: int = Query(default=80, ge=1, le=300),
    deep_limit: int = Query(default=20, ge=1, le=50),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Read-only KOL account dossier; aggregates local state without providers."""
    del staff
    try:
        return kol_account_dossier.get_kol_account_dossier(
            int(kol_pool_id),
            video_limit=video_limit,
            event_limit=event_limit,
            deep_limit=deep_limit,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/kol-pool/{kol_pool_id}/account-dossier-extract-job")
def enqueue_pool_item_account_dossier_extract(
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """Queue local account dossier materialization into independent profile_llm results."""
    try:
        return kol_account_dossier_extract.enqueue_account_dossier_extract_job(
            int(kol_pool_id),
            body=body or {},
            staff=staff,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
