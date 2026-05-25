"""Compatibility bridge for KOL product-fit preview use cases."""
from __future__ import annotations

from app.domains.kol.product_fit import (
    _CATALOG_PRODUCT_BY_SKU,
    _CATALOG_PRODUCTS,
    _catalog_product_for_sku,
    _catalog_products,
    _catalog_products_for_match,
    _compact_catalog_product,
    _dimensions11_product_fit_for_family,
    _json_write,
    _markdown_write,
    _normalize_product_fit_key,
    build_kol_product_fit_preview,
    format_preview_summary,
    get_conn,
    render_markdown,
)

__all__ = [
    "_CATALOG_PRODUCT_BY_SKU",
    "_CATALOG_PRODUCTS",
    "_catalog_product_for_sku",
    "_catalog_products",
    "_catalog_products_for_match",
    "_compact_catalog_product",
    "_dimensions11_product_fit_for_family",
    "_json_write",
    "_markdown_write",
    "_normalize_product_fit_key",
    "build_kol_product_fit_preview",
    "format_preview_summary",
    "get_conn",
    "render_markdown",
]
