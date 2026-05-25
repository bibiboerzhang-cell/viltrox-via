"""Compatibility shim for KOL pool main-linking helpers.

The implementation lives in the KOL domain.
"""
from app.domains.kol.pool_main_linking import (
    _create_main_kol_from_pool,
    _get_main_kol,
    _main_candidate_reason,
    main_candidates,
    promote_to_main,
)

__all__ = [
    "_create_main_kol_from_pool",
    "_get_main_kol",
    "_main_candidate_reason",
    "main_candidates",
    "promote_to_main",
]
