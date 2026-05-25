"""Compatibility shim for KOL competitor relation detection.

The implementation lives in the KOL domain.
"""
from app.domains.kol.competitor_detector import (
    _extract_posts,
    _post_date,
    _post_text_blob,
    _row_profile_post,
    batch_evaluate_kol_pool,
    ensure_competitor_relation_schema,
    evaluate_kol_competitor_relation,
    evaluate_kol_competitors,
    get_persisted_kol_competitors,
    persist_competitor_relations,
    persisted_competitor_dashboard,
)

__all__ = [
    "_extract_posts",
    "_post_date",
    "_post_text_blob",
    "_row_profile_post",
    "batch_evaluate_kol_pool",
    "ensure_competitor_relation_schema",
    "evaluate_kol_competitor_relation",
    "evaluate_kol_competitors",
    "get_persisted_kol_competitors",
    "persist_competitor_relations",
    "persisted_competitor_dashboard",
]
