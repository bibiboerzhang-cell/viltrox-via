"""Sales persistence input contract and unambiguous affiliate/coupon matching."""
from typing import Any

from app.domains.attribution.integrations_money import currency_code, exact_cents


def prepare_sales(orders: list[dict[str, Any]], links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    affiliates: dict[str, set[int]] = {}
    coupons: dict[str, set[int]] = {}
    for link in links:
        kol = int(link["kol_pool_id"])
        if link.get("affiliate_id"):
            affiliates.setdefault(str(link["affiliate_id"]).strip(), set()).add(kol)
        if link.get("coupon"):
            coupons.setdefault(str(link["coupon"]).strip().casefold(), set()).add(kol)
    unique: dict[str, dict[str, Any]] = {}
    for order in orders:
        sale_id = str(order.get("id") or "").strip()
        if not sale_id:
            raise ValueError("sale id missing; cannot persist idempotently")
        currency = currency_code(order.get("currency"))
        total_cents = exact_cents(order.get("total"))
        commission = order.get("commission")
        if isinstance(commission, dict):
            if commission.get("type") in {"percentage", "fixed_amount"}:
                raise ValueError("commission rate is not an earned commission amount")
            if commission.get("currency") and currency_code(commission["currency"]) != currency:
                raise ValueError("commission currency differs from sale currency")
            commission = commission.get("amount")
        commission_cents = exact_cents(commission)
        affiliate_id = str(order.get("affiliate_id") or "").strip()
        coupon = str(order.get("coupon") or "").strip().casefold()
        aid_matches = affiliates.get(affiliate_id, set())
        coupon_matches = coupons.get(coupon, set())
        # An explicit but unknown affiliate must not be reassigned by a coupon.
        matches = aid_matches if affiliate_id else coupon_matches
        if affiliate_id and coupon_matches and aid_matches != coupon_matches:
            matches = set()
        kol = next(iter(matches)) if len(matches) == 1 else None
        row = {**order, "id": sale_id, "affiliate_id": affiliate_id, "currency": currency,
               "total_cents": total_cents, "commission_cents": commission_cents, "kol_pool_id": kol}
        if sale_id in unique and unique[sale_id] != row:
            raise ValueError("conflicting duplicate sale id in provider response")
        unique[sale_id] = row
    return list(unique.values())
