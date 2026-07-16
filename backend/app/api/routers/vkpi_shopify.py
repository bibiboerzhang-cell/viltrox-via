"""V-KPI Shopify ingest routes — 真订单写入 + GMV 聚合(Attributed GMV / ROI 数据源)。

前缀 /api/admin/vkpi/shopify;读 require_tab("vkpi","read")、写 require_tab("vkpi","write"),
镜像 vkpi_inventory / vkpi_costs 的鉴权风格。
- POST /api/admin/vkpi/shopify/orders:ingest 单条或一批订单(幂等 by shop_domain+order_id)。
- GET  /api/admin/vkpi/shopify/gmv:按窗口 / 折扣码聚合真 GMV。

例外:live-creds 管理(POST /creds + POST /webhooks/register)走 require_tab("vkpi","admin"),
与 api_key_pool 同档的职责分离 —— 公司级 access_token / webhook_secret 仅 admin/owner 可改。

在拿到 live Shopify creds 之前,表 vkpi_shopify_orders 保持空 → GMV 求和为 0 →
Dashboard 继续诚实显示「待接入」。scaffold 是 real-data-ready 的,绝不编数。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.domains import business_truth
from app.domains.commerce import (
    shopify_client_credentials,
    shopify_connect,
    shopify_discounts,
    shopify_orders,
)


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
    staff=Depends(require_tab("vkpi", "admin")),
):
    """Record an authorized *manual* repair batch; never provider-confirmed GMV.

    Live provider orders enter through signed webhook/sync adapters.  This route
    is deliberately owner/admin-only, feature-gated, and requires an
    ``authorization_evidence`` object.  Rows are stamped
    ``manual_pending_verification`` and are excluded from GMV.
    """
    orders: list[dict]
    if isinstance(body, dict):
        raw = body.get("orders")
        if isinstance(raw, list):
            orders = [o for o in raw if isinstance(o, dict)]
        else:
            orders = [{key: value for key, value in body.items() if key != "authorization_evidence"}]
    else:
        raise HTTPException(status_code=400, detail="body must be an object with authorization_evidence")

    if not orders:
        raise HTTPException(status_code=400, detail="no order payload provided")
    try:
        authorization = business_truth.require_authorization_evidence(
            body,
            staff=staff,
            action="shopify_manual_order_repair",
        )
    except business_truth.BusinessTruthWriteBlocked as exc:
        status_code = 409 if exc.reason == "feature_disabled" else 400
        raise HTTPException(status_code=status_code, detail={"reason": exc.reason, "message": str(exc)}) from exc
    return _guard(
        shopify_orders.ingest_orders,
        orders,
        ingest_class="manual_unverified",
        authorization_evidence=authorization,
    )


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


# --- credentials: formal Client Credentials + legacy compatibility -----------

@router.post("/creds")
def save_shopify_creds(
    body=Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "admin")),
):
    """Advanced compatibility path for an existing long-lived access token.

    New stores should use ``/client-credentials/connect``.  This admin-only
    endpoint remains for migration and never echoes plaintext token material.
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    return _guard(shopify_connect.save_credentials, body, staff)


@router.post("/client-credentials/connect")
def connect_shopify_client_credentials(
    body=Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "admin")),
):
    """Atomically enable a candidate after token, probe and webhooks pass.

    The candidate stays out of the singleton until every external stage
    succeeds. Any failure preserves last-known-good and returns a secret-free
    four-stage receipt. Partially created webhooks are cleaned up best effort.
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    return _guard(shopify_client_credentials.connect_client_credentials, body, staff)


@router.get("/creds")
def get_shopify_creds(
    staff=Depends(require_tab("vkpi", "read")),
):
    """Masked Shopify connection status {shop_domain, token_configured, ..., source}."""
    return _guard(shopify_connect.connection_status)


@router.post("/probe")
def probe_shopify_connection(
    staff=Depends(require_tab("vkpi", "admin")),
):
    """Explicit read-only Shopify Admin API canary.

    Saving a token is configuration only.  This endpoint is the separate,
    operator-triggered proof step that may advance the encrypted credential row
    to ``connected`` after Shopify returns a valid shop identity.
    """
    return _guard(shopify_connect.probe_connection)


@router.post("/webhooks/register")
def register_shopify_webhooks(
    staff=Depends(require_tab("vkpi", "admin")),
):
    """Register ORDERS_CREATE/UPDATED + REFUNDS_CREATE webhooks. no creds -> ok:false, no throw.

    admin-gated (not write): consumes the live creds saved above, so it sits at
    the same separation-of-duties bar as /creds.
    """
    return _guard(shopify_connect.register_webhooks)


# --- creds-ready: KOL discount codes + promo links ----------------------------

@router.post("/discounts")
def create_shopify_discount(
    body=Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
):
    """Create a KOL discount code + build its promo link.

    body: {code, title?, value, value_type?, product_handle?, product_ids?,
           usage_limit?, starts_at?, ends_at?, utm_campaign?, kol_id?, project_id?}
    no creds -> {ok:false, reason:'not_configured'} (never fabricates a discount id).
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    code = str(body.get("code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="code is required")
    if body.get("value") in (None, ""):
        raise HTTPException(status_code=400, detail="value is required")
    return _guard(
        shopify_discounts.create_kol_discount,
        code=code,
        value=body.get("value"),
        title=body.get("title"),
        value_type=str(body.get("value_type") or "percentage"),
        product_handle=body.get("product_handle"),
        product_ids=body.get("product_ids"),
        usage_limit=body.get("usage_limit"),
        starts_at=body.get("starts_at"),
        ends_at=body.get("ends_at"),
        utm_campaign=str(body.get("utm_campaign") or ""),
        kol_id=body.get("kol_id"),
        project_id=body.get("project_id"),
        store_domain=body.get("store_domain"),
    )


@router.get("/discount-link")
def get_shopify_discount_link(
    product_handle: str = Query(...),
    code: str = Query(...),
    store_domain: str | None = Query(default=None),
    utm_source: str = Query(default="kol"),
    utm_medium: str = Query(default="affiliate"),
    utm_campaign: str = Query(default=""),
    staff=Depends(require_tab("vkpi", "read")),
):
    """Pure builder (no network): {discount_link}."""
    link = _guard(
        shopify_discounts.build_discount_link,
        store_domain=store_domain,
        product_handle=product_handle,
        code=code,
        utm_source=utm_source,
        utm_medium=utm_medium,
        utm_campaign=utm_campaign,
    )
    return {"discount_link": link}
