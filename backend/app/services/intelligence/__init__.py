"""Product, competitor, market, and official-matrix intelligence services.

The package facade stays lazy so importing an individual scanner service does
not eagerly assemble the official-matrix path back through DeepSight.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any


__all__ = [
    "fetch_bh_viltrox_products",
    "fetch_bh_product_reviews",
    "fetch_bh_reviews",
    "normalize_bh_product",
    "normalize_bh_review",
    "save_bh_snapshot",
    "get_latest_bh_products",
    "get_bh_summary",
    "get_bh_price_history",
    "get_bh_reviews_summary",
    "get_bh_top_rated",
    "select_bh_review_targets",
    "upsert_bh_reviews",
    "build_viltrox_overview",
    "reset_viltrox_official_roster",
    "scan_viltrox_official_matrix_now",
]

_EXPORTS = {
    "fetch_bh_viltrox_products": "app.services.intelligence.bh_scraper",
    "fetch_bh_product_reviews": "app.services.intelligence.bh_scraper",
    "fetch_bh_reviews": "app.services.intelligence.bh_scraper",
    "normalize_bh_product": "app.services.intelligence.bh_scraper",
    "normalize_bh_review": "app.services.intelligence.bh_scraper",
    "save_bh_snapshot": "app.services.intelligence.bh_repository",
    "get_latest_bh_products": "app.services.intelligence.bh_repository",
    "get_bh_summary": "app.services.intelligence.bh_repository",
    "get_bh_price_history": "app.services.intelligence.bh_repository",
    "get_bh_reviews_summary": "app.services.intelligence.bh_repository",
    "get_bh_top_rated": "app.services.intelligence.bh_repository",
    "select_bh_review_targets": "app.services.intelligence.bh_repository",
    "upsert_bh_reviews": "app.services.intelligence.bh_repository",
    "build_viltrox_overview": "app.services.intelligence.viltrox_matrix",
    "reset_viltrox_official_roster": "app.services.intelligence.viltrox_matrix",
    "scan_viltrox_official_matrix_now": "app.services.intelligence.viltrox_matrix",
}


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(name)
    module = import_module(_EXPORTS[name])
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
