"""Commerce services package; submodules load only when explicitly requested."""
from __future__ import annotations

from importlib import import_module
from types import ModuleType

__all__ = ["orders", "attribution", "payouts"]


def __getattr__(name: str) -> ModuleType:
    """Preserve package attribute access without eager service imports."""

    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return import_module(f"{__name__}.{name}")


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
