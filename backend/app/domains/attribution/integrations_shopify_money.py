"""Shopify delta-refund accounting: settled transactions, one currency, no FX."""
from typing import Any

from app.domains.attribution.integrations_money import currency_code, exact_cents


def refund_money(payload: dict[str, Any], order_currency: str = "") -> tuple[int, str]:
    currency = str(order_currency or payload.get("currency") or "").strip().upper()
    transactions = payload.get("transactions")
    if isinstance(transactions, list):
        total = 0
        settled = False
        for transaction in transactions:
            if not isinstance(transaction, dict):
                raise ValueError("invalid refund transaction")
            if str(transaction.get("kind") or "").lower() != "refund" or str(transaction.get("status") or "").lower() != "success":
                continue
            money_set = transaction.get("amount_set") or {}
            shop_money = money_set.get("shop_money") if isinstance(money_set, dict) else None
            if isinstance(shop_money, dict):
                amount = shop_money.get("amount")
                item_currency = currency_code(shop_money.get("currency_code"))
            else:
                amount = transaction.get("amount")
                item_currency = currency_code(transaction.get("currency") or currency)
            if currency and item_currency != currency:
                raise ValueError("refund currency differs from order currency; FX reconciliation required")
            currency = item_currency
            cents = exact_cents(amount)
            if cents < 0:
                raise ValueError("refund transaction amount must be non-negative")
            total += cents
            settled = True
        if not settled:
            raise ValueError("refund has no successful refund transaction")
        return total, currency_code(currency)
    # Explicit delta is supported for legacy signed payloads. Cumulative order
    # totals are never a refund event: replaying them would subtract twice.
    amount = payload.get("refund_amount", payload.get("amount"))
    if amount is None:
        raise ValueError("refund delta missing; cumulative total_refunded is not an event amount")
    if isinstance(amount, dict):
        shop = amount.get("shop_money") or {}
        if not isinstance(shop, dict) or "amount" not in shop:
            raise ValueError("refund requires shop_money amount and currency")
        item_currency = currency_code(shop.get("currency_code"))
        if currency and currency != item_currency:
            raise ValueError("refund currency differs from order currency")
        currency, amount = item_currency, shop["amount"]
    cents = exact_cents(amount)
    if cents < 0:
        raise ValueError("refund amount must be non-negative")
    return cents, currency_code(currency)
