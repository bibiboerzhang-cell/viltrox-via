"""Compatibility facade for KOL intelligence card aggregation."""
from app.domains.kol.intelligence_card import (
    _decision_support,
    _product_fit,
    build_kol_pool_intelligence_card,
    kol_product_fit,
)

__all__ = [
    "_decision_support",
    "_product_fit",
    "build_kol_pool_intelligence_card",
    "kol_product_fit",
]
