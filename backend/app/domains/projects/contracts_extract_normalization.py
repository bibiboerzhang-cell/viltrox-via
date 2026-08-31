"""Field normalization helpers for project contract extraction."""
from __future__ import annotations

from typing import Any, Callable, Collection


def _normalized_fee(value: Any, math_module: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("must be a number or null")
    amount = float(value)
    if not math_module.isfinite(amount) or amount < 0:
        raise ValueError("must be a finite non-negative number")
    return amount


def _normalized_count(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TypeError("must be a non-negative integer or null")
    return int(value)


def _normalized_date(value: Any, date_type: Any, date_parser: Callable[[Any], str | None]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("must be an ISO date string or null")
    if not value.strip():
        return None
    normalized = date_parser(value)
    if not normalized:
        raise ValueError("must contain an ISO date")
    try:
        date_type.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("must contain a valid ISO date") from exc
    return normalized


def _normalized_deadline(value: Any, datetime_type: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("must be an ISO date/time string or null")
    normalized = value.strip()
    if not normalized:
        return None
    try:
        datetime_type.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("must be a valid ISO date/time") from exc
    return normalized


def _normalized_string_list(value: Any) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise TypeError("must be a list of strings")
    return [item.strip() for item in value if item.strip()]


def _normalized_deliverables(value: Any) -> list[Any]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise TypeError("must be a list of objects")
    for item in value:
        for text_field in ("platform", "content_type", "notes"):
            if text_field in item and not isinstance(item[text_field], str):
                raise TypeError(f"{text_field} must be a string")
        quantity = item.get("quantity")
        if quantity is not None and (
            isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0
        ):
            raise TypeError("quantity must be a non-negative integer or null")
        deadline = item.get("deadline")
        if deadline is not None and not isinstance(deadline, str):
            raise TypeError("deadline must be a string or null")
    return value


def normalized_business_field(
    field: str,
    value: Any,
    *,
    text_fields: Collection[str],
    math_module: Any,
    date_type: Any,
    datetime_type: Any,
    date_parser: Callable[[Any], str | None],
) -> Any:
    if field == "fee_amount":
        return _normalized_fee(value, math_module)

    if field == "deliverable_count":
        return _normalized_count(value)

    if field in {"start_date", "end_date"}:
        return _normalized_date(value, date_type, date_parser)

    if field == "promised_publish_deadline":
        return _normalized_deadline(value, datetime_type)

    if field in text_fields:
        if not isinstance(value, str):
            raise TypeError("must be a string")
        return value.strip()

    if field in {"platforms", "must_include"}:
        return _normalized_string_list(value)

    if field == "deliverables":
        return _normalized_deliverables(value)

    raise KeyError(field)

__all__ = ["normalized_business_field"]
