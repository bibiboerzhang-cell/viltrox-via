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
        "price_usd": product.get("price_usd"),
        "description": str(product.get("description") or ""),
        "specs_line": specs_line(product),
        "match_score": list(match_score),
    }
