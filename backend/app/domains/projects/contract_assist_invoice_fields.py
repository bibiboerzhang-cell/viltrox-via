"""Pure field normalization for invoice extraction results."""
from __future__ import annotations

from typing import Any


def normalized_invoice_fields(data: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    extracted: dict[str, Any] = {}
    for field in fields:
        value = data.get(field)
        if field == "amount":
            try:
                extracted[field] = float(value) if value not in (None, "") else None
            except (TypeError, ValueError):
                extracted[field] = None
        else:
            extracted[field] = str(value or "").strip()[:500]
    return extracted
