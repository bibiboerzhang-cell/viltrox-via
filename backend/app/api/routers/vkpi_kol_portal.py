"""KOL 自助门户(只读·token 隔离)API。

两个 router 共一个模块(镜像 vkpi.py 的 router + public_router + webhook_router):

  portal_public_router  公开(免 admin 登录,但 token 必填):
    GET /api/portal/kol/{token}
      → resolve_token → get_portal_data;token 无效/撤销/不存在 → 404。
      只读,无任何 body / 写动词。安全红线:只返回 token 持有者(单个 KOL)自己的数据。

  router  管理端(require_tab vkpi write)发链:
    POST /api/admin/vkpi/kol-portal/{kol_id}/issue-token
      → issue_token(幂等复用),返回 {token, url}。kol_id 即 kol_pool_id。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies.perms import require_tab
from app.db.connection import get_conn
from app.domains.access import scope
from app.domains.kol import portal

# 公开门户(挂在 main.py 角色门之外,免 admin 登录即可达)
portal_public_router = APIRouter(prefix="/api/portal/kol", tags=["vkpi-kol-portal-public"])

# 管理端发链(仅 IS_ADMIN_APP)
router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-kol-portal"])


@portal_public_router.get("/{token}")
def get_kol_portal(token: str) -> dict[str, Any]:
    """公开只读门户:凭 token 看「自己的」点击/订单/GMV/被分配项目。

    无认证依赖(public)。token 无效/撤销/不存在或 pool 行已删 → 404(不泄露存在性)。
    """
    conn = get_conn()
    kol_pool_id = portal.resolve_token(conn, token)
    if kol_pool_id is None:
        raise HTTPException(status_code=404, detail="invalid or expired link")
    data = portal.get_portal_data(conn, kol_pool_id)
    if data is None:
        raise HTTPException(status_code=404, detail="invalid or expired link")
    return data


@router.post("/kol-portal/{kol_id}/issue-token")
def issue_kol_portal_token(
    kol_id: int,
    staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    """为某 KOL(kol_id 即 kol_pool_id)发放门户 token;幂等复用未撤销的 live token。

    返回 {token, url},url 指向前端 standalone 门户路由。
    """
    conn = get_conn()
    token = portal.issue_token(
        conn,
        kol_id,
        created_by_staff_id=scope.actor_staff_id(staff) or None,
    )
    return {"token": token, "url": f"/portal/kol/{token}"}
