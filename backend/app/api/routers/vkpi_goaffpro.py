"""V-KPI GOAFFPRO routes — Affiliate 接入只读骨架(D1,无 key 可建)。

前缀 /api/admin/vkpi/goaffpro;镜像 vkpi_shopify 的鉴权与 separation-of-duties:
- 读连接状态 GET /creds:require_tab("vkpi","read"),只回 masked。
- 写 creds POST /creds:require_tab("vkpi","admin")(公司级 token 仅 admin/owner 可改,
  与 api_key_pool / shopify creds 同档)。
- 手动 sync stub POST /sync:require_tab("vkpi","admin"),探活拉一页,不落库不归因。
- 只读 GET /affiliates、GET /orders:require_tab("vkpi","read"),薄透传 REST client。

无真 GOAFFPRO key 时,connection_status -> not_configured,各 list 端点 ->
{ok:false, reason:'not_configured'},Dashboard 继续诚实显示「待接入」。绝不编数。
**本刀只加 GOAFFPRO,不删/不隐藏现有自建 Links**(下一刀做)。

字段映射「待 key 校准」见 domains/integrations/goaffpro_connect.py。
与 KOL 评分域物理隔离:无 viltrox_fit_score / rule_v0 触点。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.domains.integrations import goaffpro_connect


router = APIRouter(prefix="/api/admin/vkpi/goaffpro", tags=["vkpi-goaffpro"])


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface DB/serialize errors as 500 with msg
        raise HTTPException(status_code=500, detail=f"goaffpro error: {exc}") from exc


# --- creds-ready: connection creds (encrypted store + settings-page fill) -----

@router.post("/creds")
def save_goaffpro_creds(
    body=Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "admin")),
):
    """Persist GOAFFPRO creds (encrypted). Returns masked-only; never echoes plaintext token.

    admin-gated: mirrors api_key_pool / shopify creds separation-of-duties —
    only admin/owner may manage company-wide live tokens.
    body: {access_token, public_token?, private_token?, api_base?}
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    return _guard(goaffpro_connect.save_credentials, body, staff)


@router.get("/creds")
def get_goaffpro_creds(
    staff=Depends(require_tab("vkpi", "read")),
):
    """Masked GOAFFPRO connection status {api_base, access_token_configured, ..., source}."""
    return _guard(goaffpro_connect.connection_status)


# --- read-only: thin REST passthrough (creds-ready) ---------------------------

@router.get("/affiliates")
def list_goaffpro_affiliates(
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int | None = Query(default=None, ge=0),
    staff=Depends(require_tab("vkpi", "read")),
):
    """List affiliates. no creds -> {ok:false, reason:'not_configured'}. 字段映射待 key 校准。"""
    return _guard(goaffpro_connect.list_affiliates, limit=limit, offset=offset)


@router.get("/orders")
def list_goaffpro_orders(
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int | None = Query(default=None, ge=0),
    staff=Depends(require_tab("vkpi", "read")),
):
    """List affiliate orders. no creds -> {ok:false, reason:'not_configured'}. 字段映射待 key 校准。"""
    return _guard(goaffpro_connect.list_orders, limit=limit, offset=offset)


# --- manual sync stub (D1: probe-only, no persistence/attribution yet) --------

@router.post("/sync")
def sync_goaffpro(
    staff=Depends(require_tab("vkpi", "admin")),
):
    """Manual sync stub — probe one page of affiliates + orders. no creds -> ok:false, no throw.

    D1 骨架:仅探活,不落库、不归因(落账/折扣码映射是后续刀)。
    """
    return _guard(goaffpro_connect.sync_stub)
