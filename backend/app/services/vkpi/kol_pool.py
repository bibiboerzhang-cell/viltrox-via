"""Compatibility facade for KOL pool use cases.

The implementation lives in the KOL domain. Keep this module only for legacy
imports through ``app.services.vkpi`` while routers and jobs migrate.
"""
from app.domains.kol.pool import (
    batch_enrich_items,
    enrich_item,
    get_item,
    import_items,
    list_pool,
    main_candidates,
    promote_to_main,
    summary,
)
from app.domains.kol.pool_common import (
    COUNTRY_NAMES,
    ENRICHABLE_PLATFORMS,
    KOL_POOL_LIST_COLUMNS,
    _clear_kol_pool_read_cache,
    _country_code,
    _normalize_item,
)

__all__ = [
    "COUNTRY_NAMES",
    "ENRICHABLE_PLATFORMS",
    "KOL_POOL_LIST_COLUMNS",
    "_clear_kol_pool_read_cache",
    "_country_code",
    "_normalize_item",
    "batch_enrich_items",
    "enrich_item",
    "get_item",
    "import_items",
    "list_pool",
    "main_candidates",
    "promote_to_main",
    "summary",
]
