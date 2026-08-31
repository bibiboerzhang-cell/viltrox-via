"""Rate-limiting and security facade with lazy backend initialization."""
from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "rate_limit",
    "check_rate_limit",
    "get_client_ip",
    "get_rate_limit_stats",
    "cleanup_old_buckets",
]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    backend = import_module(f"{__name__}.rate_limiter")
    return getattr(backend, name)


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
