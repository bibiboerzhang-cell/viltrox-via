"""Shared cost ledger constants and value helpers."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

VALID_COST_TYPES = {"product", "shipping", "cash_fee", "customs_tax", "sample", "overhead", "other"}
TYPE_ALIASES = {
    "sample_cost": "sample",
    "sample_product": "sample",
    "product_cost": "product",
    "shipping_fee": "shipping",
    "logistics": "shipping",
    "customs": "customs_tax",
    "tax": "customs_tax",
    "cash": "cash_fee",
}


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _amount_cents(body: dict[str, Any]) -> int:
    if "amount_cents" in body:
        return _int(body.get("amount_cents"))
    amount = body.get("amount_usd", body.get("amount", 0))
    try:
        return int(round(float(amount or 0) * 100))
    except (TypeError, ValueError):
        return 0


def _sku(value: Any) -> str:
    return str(value or "").strip().upper()
