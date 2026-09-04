"""Replay-safe product identity for SKU-optional KOL search sessions."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


_TEXT_FIELDS = (
    "sku",
    "model_name",
    "marketing_name",
    "category_main",
    "category_detail",
    "series",
    "mount",
    "resolved_model_code",
    "resolved_model_identity",
    "resolved_alias",
    "resolved_canonical",
    "resolved_variant",
    "requested_aperture",
)
_CODE_FIELDS = ("resolution_kind", "resolution_basis", "alias_resolution_basis")
_SIZE_FIELDS = ("focal_family_size", "model_family_size", "product_family_size")
_LIST_FIELDS = (
    "focal_family_skus",
    "model_family_skus",
    "product_family_skus",
    "focal_family_mounts",
)


def project_resolved_product(
    value: Any,
    *,
    dict_value: Callable[[Any], dict[str, Any]],
    list_value: Callable[[Any], list[Any]],
    safe_text: Callable[..., str],
    safe_code: Callable[[Any], str],
    int_or_none: Callable[[Any], int | None],
    float_or_none: Callable[[Any], float | None],
) -> dict[str, Any]:
    """Keep a bounded family identity even when no exact SKU was selected."""

    raw = dict_value(value)
    output: dict[str, Any] = {}
    for name in _TEXT_FIELDS:
        text = safe_text(raw.get(name), limit=240)
        if text:
            output[name] = text
    for name in _CODE_FIELDS:
        code = safe_code(raw.get(name))
        if code:
            output[name] = code

    focal = int_or_none(raw.get("focal_mm"))
    if focal is not None and 1 <= focal <= 1000:
        output["focal_mm"] = focal
    for name in _SIZE_FIELDS:
        size = int_or_none(raw.get(name))
        if size is not None and 1 <= size <= 500:
            output[name] = size

    for name in _LIST_FIELDS:
        values: list[str] = []
        seen: set[str] = set()
        for item in list_value(raw.get(name))[:24]:
            text = safe_text(item, limit=240)
            key = text.casefold()
            if text and key not in seen:
                seen.add(key)
                values.append(text)
            if len(values) >= 12:
                break
        if values:
            output[name] = values

    price = float_or_none(raw.get("price_usd"))
    if price is not None and 0 <= price <= 1_000_000:
        output["price_usd"] = price
    return output


__all__ = ["project_resolved_product"]
