"""Redis-first cache facade with lazy backend initialization."""
from __future__ import annotations

from importlib import import_module
from typing import Any

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


def __getattr__(name: str) -> Any:
    """Load the cache backend only when an exported operation is requested."""

    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    backend = import_module(f"{__name__}.memory_cache")
    return getattr(backend, name)


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
