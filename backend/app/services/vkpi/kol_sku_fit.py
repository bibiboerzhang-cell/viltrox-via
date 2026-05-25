"""Compatibility bridge for KOL x SKU fit use cases."""
from __future__ import annotations

from app.domains.kol.sku_fit import (
    _fetch_aliases_by_sku,
    _fetch_kol,
    _fetch_sku_facts,
    _load_profile_product_fit,
    _norm,
    _profile_component,
    _score_sku,
    build_kol_sku_fit_report,
    select_default_kol_id,
)

__all__ = [
    "_fetch_aliases_by_sku",
    "_fetch_kol",
    "_fetch_sku_facts",
    "_load_profile_product_fit",
    "_norm",
    "_profile_component",
    "_score_sku",
    "build_kol_sku_fit_report",
    "select_default_kol_id",
]
