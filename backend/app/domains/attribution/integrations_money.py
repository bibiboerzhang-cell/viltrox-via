"""Exact connector accounting amounts (hundredths of the stated currency, no FX)."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Any


def currency_code(value: Any) -> str:
    code = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", code) or code in {"XXX", "XTS"}:
        raise ValueError("an explicit currency code is required")
    return code


def exact_cents(value: Any, *, already_cents: bool = False) -> int:
    if isinstance(value, bool) or value in (None, ""):
        raise ValueError("amount is missing or invalid")
    try:
        amount = Decimal(str(value).strip().replace(",", ""))
        if not amount.is_finite():
            raise ValueError("amount must be finite")
        scaled = amount if already_cents else amount * 100
        rounded = scaled.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        if already_cents and scaled != rounded:
            raise ValueError("cents must be an integer")
        if abs(rounded) > 9_000_000_000_000_000:
            raise ValueError("amount exceeds accounting bounds")
        return int(rounded)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("invalid decimal amount") from exc
