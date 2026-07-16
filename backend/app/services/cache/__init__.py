"""
services/cache — Redis-first cache utilities for Viltrox 2.0
"""
from app.services.cache.memory_cache import (
    cache_get,
    cache_get_or_build,
    cache_set,
    cache_delete,
    cache_clear,
    cache_invalidate_admin,
    cached,
    get_cache_stats,
)

__all__ = [
    "cache_get",
    "cache_get_or_build",
    "cache_set",
    "cache_delete",
    "cache_clear",
    "cache_invalidate_admin",
    "cached",
    "get_cache_stats",
]
