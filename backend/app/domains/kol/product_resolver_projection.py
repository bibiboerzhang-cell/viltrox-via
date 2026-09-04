"""Bounded public projections for resolved catalog products."""
from __future__ import annotations

from typing import Any


def focal_suggestions(rows: Any, *, limit: int = 6) -> list[dict[str, Any]]:
    return [
        {
            "sku": row.get("sku"),
            "name": row.get("marketing_name") or row.get("model_name"),
            "mount": row.get("mount"),
            "series": row.get("series"),
        }
        for row in list(rows or [])[: max(1, int(limit))]
    ]


def format_aperture(aperture: tuple[str, float]) -> str:
    kind, value = aperture
    return f"{kind.upper()}{value:g}"


def specs_line(product: dict[str, Any]) -> str:
    """Build the compact English catalog summary used by planner prompts."""

    parts: list[str] = []
    name = str(product.get("marketing_name") or product.get("model_name") or "").strip()
    if name:
        parts.append(name)
    price = product.get("price_usd")
    try:
        if price is not None and float(price) > 0:
            parts.append(f"${float(price):,.0f} USD")
    except (TypeError, ValueError):
        pass
    category = str(product.get("category_main") or "").strip()
    detail = str(product.get("category_detail") or "").strip()
    series = str(product.get("series") or "").strip()
    category_bits = [
        bit
        for bit in (category, detail if detail and detail != category else "", series)
        if bit
    ]
    if category_bits:
        parts.append("category: " + " / ".join(dict.fromkeys(category_bits)))
    description = " ".join(str(product.get("description") or "").split())[:280]
    if description:
        parts.append(description)
    return " · ".join(parts)


def public_product_projection(
    product: dict[str, Any],
    *,
    match_score: tuple[int, int, int],
) -> dict[str, Any]:
    """Return the catalog fields shared by text and exact-SKU resolution."""

    return {
        "sku": str(product.get("sku") or ""),
        "model_name": str(product.get("model_name") or ""),
        "marketing_name": str(product.get("marketing_name") or ""),
        "category_main": str(product.get("category_main") or ""),
        "category_detail": str(product.get("category_detail") or ""),
        "series": str(product.get("series") or ""),
        "mount": str(product.get("mount") or ""),
        "price_usd": product.get("price_usd"),
        "description": str(product.get("description") or ""),
        "specs_line": specs_line(product),
        "match_score": list(match_score),
    }


_PLANNER_METADATA_KEYS = (
    "resolution_kind",
    "resolution_basis",
    "requested_aperture",
    "focal_mm",
    "focal_family_size",
    "focal_family_mounts",
    "focal_family_skus",
    "model_family_skus",
    "product_family_skus",
    "model_family_size",
    "product_family_size",
    "resolved_model_code",
    "resolved_model_identity",
    "resolved_alias",
    "resolved_canonical",
    "resolved_variant",
    "alias_resolution_basis",
)


def planner_product_projection(product: dict[str, Any]) -> dict[str, Any]:
    """Project a resolved product without dropping safe family/alias evidence."""

    try:
        price = float(product["price_usd"]) if product.get("price_usd") is not None else None
    except (TypeError, ValueError):
        price = None
    projected = {
        "sku": product.get("sku"),
        "model_name": product.get("model_name"),
        "marketing_name": product.get("marketing_name"),
        "category_main": product.get("category_main"),
        "category_detail": product.get("category_detail"),
        "series": product.get("series"),
        "mount": product.get("mount"),
        "price_usd": price,
    }
    for key in _PLANNER_METADATA_KEYS:
        value = product.get(key)
        if isinstance(value, list):
            projected[key] = [str(item) for item in value if str(item).strip()][:12]
        elif isinstance(value, (str, int, float)) and value != "":
            projected[key] = value
    return projected
