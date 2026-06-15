"""V-KPI 经销商地图(Dealer Map)路由 —— 美国相机零售商地理数据源。

前缀 /api/admin/vkpi/dealers;读 require_tab("vkpi","read")、写 require_tab("vkpi","write"),
镜像 vkpi_shopify / vkpi_inventory 的鉴权风格。
- GET  /api/admin/vkpi/dealers           列出经销商(limit / state 过滤)。
- GET  /api/admin/vkpi/dealers/locations  扁平 pin 数组(仅 lat/lng 齐全),点亮前端
  Dealers 地图模式(viewModes.dealers.apiEndpoint 已指向本路径,available:false 待接入)。
- POST /api/admin/vkpi/dealers/scrape-enqueue  有界触发(record_only 默认 = 纯预检,no blast)。

纯地理数据,无 touches_v6_fit;与 KOL Pool / 评分域物理隔离,绝不碰 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.domains.commerce import dealer_scrape


router = APIRouter(prefix="/api/admin/vkpi/dealers", tags=["vkpi-dealers"])


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface DB/scrape errors as 500 with msg
        raise HTTPException(status_code=500, detail=f"dealer error: {exc}") from exc


@router.get("")
def list_dealers_route(
    limit: int = Query(default=100, ge=1, le=500),
    state: str | None = Query(default=None),
    staff=Depends(require_tab("vkpi", "read")),
):
    """List dealers {dealers:[{id,name,address,city,state,lat,lng,source,created_at}]}."""
    dealers = _guard(dealer_scrape.list_dealers, limit=limit, state=state)
    return {"dealers": dealers}


@router.get("/locations")
def dealer_locations_route(
    staff=Depends(require_tab("vkpi", "read")),
):
    """Flat pin array (lat/lng-present only) for the Dealers map mode.

    Returns {pins:[{name,address,city,state,lat,lng,color}]}.
    """
    pins = _guard(dealer_scrape.list_dealer_pins)
    return {"pins": pins}


@router.post("/scrape-enqueue")
def scrape_enqueue_route(
    body=Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
):
    """Bounded dealer scrape trigger (single batch <= 20).

    Body {source?, limit?(<=20), record_only?(default true)}. record_only=true returns
    a plan only — no network blast, no DB write. Returns
    {ok,source,requested,inserted,skipped,geocoded,pending_geocode,errors:[],...}.
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    source = body.get("source")
    limit = body.get("limit", dealer_scrape._MAX_BATCH)
    record_only = body.get("record_only", True)
    return _guard(
        dealer_scrape.scrape_dealers_enqueue,
        limit=limit,
        record_only=bool(record_only),
        source=source,
    )
