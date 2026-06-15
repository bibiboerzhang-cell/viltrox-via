"""V-KPI Shopify ingest routes — 真订单写入 + GMV 聚合(Attributed GMV / ROI 数据源)。

前缀 /api/admin/vkpi/shopify;读 require_tab("vkpi","read")、写 require_tab("vkpi","write"),
镜像 vkpi_inventory / vkpi_costs 的鉴权风格。
- POST /api/admin/vkpi/shopify/orders:ingest 单条或一批订单(幂等 by shop_domain+order_id)。
- GET  /api/admin/vkpi/shopify/gmv:按窗口 / 折扣码聚合真 GMV。

在拿到 live Shopify creds 之前,表 vkpi_shopify_orders 保持空 → GMV 求和为 0 →
Dashboard 继续诚实显示「待接入」。scaffold 是 real-data-ready 的,绝不编数。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.domains.commerce import shopify_orders


router = APIRouter(prefix="/api/admin/vkpi/shopify", tags=["vkpi-shopify"])


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface DB/serialize errors as 500 with msg
        raise HTTPException(status_code=500, detail=f"shopify ingest error: {exc}") from exc


@router.post("/orders")
def ingest_orders(
    body=Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
):
    """Ingest one order (dict) or a batch (list, or {"orders": [...]}). Idempotent."""
    orders: list[dict]
    if isinstance(body, list):
        orders = [o for o in body if isinstance(o, dict)]
    elif isinstance(body, dict):
        raw = body.get("orders")
        if isinstance(raw, list):
            orders = [o for o in raw if isinstance(o, dict)]
        else:
            orders = [body]
    else:
        raise HTTPException(status_code=400, detail="body must be an order object or a list of orders")

    if not orders:
        raise HTTPException(status_code=400, detail="no order payload provided")

    return _guard(shopify_orders.ingest_orders, orders)


@router.get("/gmv")
def get_gmv(
    window_days: int | None = Query(default=None, ge=1, le=3650),
    discount_code: list[str] | None = Query(default=None),
    staff=Depends(require_tab("vkpi", "read")),
):
    """Real attributed GMV summary {gmv_cents, order_count, currency}."""
    return _guard(
        shopify_orders.summarize_gmv,
        window_days=window_days,
        discount_codes=discount_code,
    )
