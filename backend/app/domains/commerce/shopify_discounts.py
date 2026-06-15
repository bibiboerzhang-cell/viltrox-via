"""Shopify discounts — 自动建折扣码(Admin GraphQL)+ 折扣链 builder(creds-ready)。

- create_discount_code(...): 调 discountCodeBasicCreate mutation 建码;无 creds → {ok:False,
  reason:'not_configured'},绝不抛、绝不编造 discount_id。
- build_discount_link(...): 纯函数,无网络,单测无需 mock;product handle + discount + utm。
- create_kol_discount(...): create_discount_code + build_discount_link 串起来,
  回 {ok, code, discount_link, shopify_discount_id, errors?, reason?}。
- 所有外呼复用 shopify_connect.post_graphql(httpx 直连,统一 try/catch)。绝不烧 LLM。

与 KOL 评分域物理隔离:无 viltrox_fit_score / rule_v0 触点。
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode

from app.domains.commerce import shopify_connect

_DISCOUNT_MUTATION = """
mutation discountCodeBasicCreate($basicCodeDiscount: DiscountCodeBasicInput!) {
  discountCodeBasicCreate(basicCodeDiscount: $basicCodeDiscount) {
    codeDiscountNode { id }
    userErrors { field message }
  }
}
""".strip()


def _norm_domain(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text.strip("/").split("/")[0]


def _customer_gets_value(value: Any, value_type: str) -> dict[str, Any]:
    """Build the Shopify customerGets.value object for percentage or fixed_amount."""
    if value_type == "fixed_amount":
        # discountAmount.amount is a money string; appliesOnEachItem false = order-level.
        amount = f"{float(value):.2f}"
        return {"discountAmount": {"amount": amount, "appliesOnEachItem": False}}
    # percentage is a 0..1 fraction in the Admin API (e.g. 0.15 == 15% off).
    pct = float(value)
    if pct > 1:
        pct = pct / 100.0
    return {"percentage": pct}


def create_discount_code(
    *,
    code: str,
    title: str | None = None,
    value: float,
    value_type: str = "percentage",
    product_ids: list[str] | None = None,
    starts_at: str | None = None,
    ends_at: str | None = None,
    usage_limit: int | None = None,
) -> dict[str, Any]:
    """Create a basic Shopify discount code via Admin GraphQL.

    creds-ready: no creds -> {ok:False, reason:'not_configured'} without fabricating
    a discount_id. Returns {ok, code, discount_id, errors?}.
    """
    code = str(code or "").strip()
    if not code:
        raise ValueError("code is required")
    vtype = str(value_type or "percentage").strip().lower()
    if vtype not in {"percentage", "fixed_amount"}:
        raise ValueError("value_type must be 'percentage' or 'fixed_amount'")

    items: dict[str, Any]
    if product_ids:
        # product_ids may be GIDs or numeric ids; pass GIDs through, wrap raw ids.
        gids = [
            pid if str(pid).startswith("gid://") else f"gid://shopify/Product/{pid}"
            for pid in product_ids
            if str(pid or "").strip()
        ]
        items = {"products": {"productsToAdd": gids}}
    else:
        items = {"all": True}

    basic: dict[str, Any] = {
        "title": str(title or code),
        "code": code,
        "customerSelection": {"all": True},
        "customerGets": {"value": _customer_gets_value(value, vtype), "items": items},
        "appliesOncePerCustomer": True,
    }
    if starts_at:
        basic["startsAt"] = starts_at
    if ends_at:
        basic["endsAt"] = ends_at
    if usage_limit:
        try:
            basic["usageLimit"] = int(usage_limit)
        except (TypeError, ValueError):
            pass

    result = shopify_connect.post_graphql(_DISCOUNT_MUTATION, {"basicCodeDiscount": basic})
    if not result.get("ok"):
        out: dict[str, Any] = {"ok": False, "code": code}
        if result.get("reason"):
            out["reason"] = result["reason"]
        if result.get("errors"):
            out["errors"] = result["errors"]
        if result.get("error"):
            out["error"] = result["error"]
        return out

    payload = ((result.get("data") or {}).get("discountCodeBasicCreate")) or {}
    user_errors = payload.get("userErrors") or []
    node = payload.get("codeDiscountNode") or {}
    discount_id = node.get("id")
    if user_errors or not discount_id:
        return {"ok": False, "code": code, "errors": user_errors or [{"message": "discount creation returned no id"}]}
    return {"ok": True, "code": code, "discount_id": discount_id}


def build_discount_link(
    *,
    store_domain: str | None = None,
    product_handle: str,
    code: str,
    utm_source: str = "kol",
    utm_medium: str = "affiliate",
    utm_campaign: str = "",
) -> str:
    """Pure builder: store/products/{handle}?discount=CODE&utm... — no network.

    store_domain defaults to the connected shop domain. Raises ValueError when no
    store_domain can be resolved or product_handle/code is missing.
    """
    handle = str(product_handle or "").strip().strip("/")
    discount_code = str(code or "").strip()
    if not handle:
        raise ValueError("product_handle is required")
    if not discount_code:
        raise ValueError("code is required")
    domain = _norm_domain(store_domain) or _norm_domain(shopify_connect.get_credentials().get("shop_domain"))
    if not domain:
        raise ValueError("store_domain is required (no connected shop)")

    params: list[tuple[str, str]] = [("discount", discount_code)]
    if utm_source:
        params.append(("utm_source", str(utm_source)))
    if utm_medium:
        params.append(("utm_medium", str(utm_medium)))
    if utm_campaign:
        params.append(("utm_campaign", str(utm_campaign)))
    query = urlencode(params)
    safe_handle = quote(handle, safe="")
    return f"https://{domain}/products/{safe_handle}?{query}"


def create_kol_discount(
    *,
    code: str,
    value: float,
    product_handle: str | None = None,
    title: str | None = None,
    value_type: str = "percentage",
    product_ids: list[str] | None = None,
    usage_limit: int | None = None,
    starts_at: str | None = None,
    ends_at: str | None = None,
    utm_campaign: str = "",
    kol_id: int | None = None,
    project_id: int | None = None,
    store_domain: str | None = None,
) -> dict[str, Any]:
    """Create a KOL discount code and build its promo link in one call.

    creds-ready: when Shopify is not configured the code is not created, but a
    deterministic discount_link is still returned when a store_domain is resolvable
    (so the link can be staged/copied). Returns
    {ok, code, discount_link, shopify_discount_id?, errors?, reason?}.
    """
    created = create_discount_code(
        code=code,
        title=title,
        value=value,
        value_type=value_type,
        product_ids=product_ids,
        starts_at=starts_at,
        ends_at=ends_at,
        usage_limit=usage_limit,
    )

    discount_link = ""
    link_error = ""
    if product_handle:
        try:
            discount_link = build_discount_link(
                store_domain=store_domain,
                product_handle=product_handle,
                code=code,
                utm_campaign=utm_campaign or (f"project_{project_id}" if project_id else (f"kol_{kol_id}" if kol_id else "")),
            )
        except ValueError as exc:
            link_error = str(exc)

    out: dict[str, Any] = {
        "ok": bool(created.get("ok")),
        "code": str(code or "").strip(),
        "discount_link": discount_link,
        "shopify_discount_id": created.get("discount_id"),
    }
    if kol_id is not None:
        out["kol_id"] = kol_id
    if project_id is not None:
        out["project_id"] = project_id
    if created.get("reason"):
        out["reason"] = created["reason"]
    if created.get("errors"):
        out["errors"] = created["errors"]
    if created.get("error"):
        out["error"] = created["error"]
    if link_error:
        out["link_error"] = link_error
    return out


__all__ = [
    "create_discount_code",
    "build_discount_link",
    "create_kol_discount",
]
