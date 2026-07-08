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

# 成本币种白名单(照 contract_generator._CURRENCY_RE 口径 + CN/HK/TW 常用币种)。add_cost
# 写前校验:非法币种 → ValueError(路由映 400),杜绝任意币种混入后被汇总直加(无换算)。
VALID_CURRENCIES = {"USD", "CNY", "RMB", "EUR", "GBP", "JPY", "AUD", "CAD", "HKD", "TWD", "SGD", "KRW"}
# 成本状态白名单:允许成本生命周期已知状态,拒绝任意串(注入/错拼)→ 400。
# 汇总口径仅计 status='actual'(见 ledger.summarize_project),estimate/pending 不再冒充实际成本。
VALID_COST_STATUSES = {"actual", "estimate", "estimated", "pending", "void"}
# amount_cents 落 BIGINT(±9.22e18)。留头寸取 9e18 为写入上界,超出即 400,
# 杜绝 amount_usd=1e17 → cents 1e19 溢出 BIGINT 触发 500。
MAX_AMOUNT_CENTS = 9_000_000_000_000_000_000


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


def validate_amount_cents(amount_cents: int) -> int:
    """金额分值写前硬校验(路由映 400):非负 + 不超 BIGINT 写入上界。

    统一收口 add_cost / update_cost / upsert_product_cost 三兄弟的越界闸:金额直入
    BIGINT 列,负数或 >9e18 会触发 PG 溢出 500(并泄露 DETAIL)。写前收敛成 ValueError。
    """
    if amount_cents < 0:
        raise ValueError("amount must be non-negative")
    if amount_cents > MAX_AMOUNT_CENTS:
        raise ValueError("amount exceeds maximum allowed")
    return amount_cents


def normalize_currency(value: Any) -> str | None:
    """校验币种在白名单并归一化为大写码;未提供(None/空串)→ None(保留调用方回退语义)。"""
    if value is None or not str(value).strip():
        return None
    code = str(value).strip().upper()
    if code not in VALID_CURRENCIES:
        raise ValueError("unsupported currency")
    return code


def normalize_cost_status(value: Any) -> str | None:
    """校验成本状态在白名单并归一化为小写;未提供 → None(保留调用方回退语义)。"""
    if value is None or not str(value).strip():
        return None
    status = str(value).strip().lower()
    if status not in VALID_COST_STATUSES:
        raise ValueError("unsupported cost status")
    return status
