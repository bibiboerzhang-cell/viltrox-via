"""System services package with explicit, lazy submodule loading.

``from app.services.system import staff`` uses Python's package-submodule
fallback, so importing the package itself does not need to initialize database,
authentication, integration, or runtime service graphs.  Keeping this file as
a declaration-only leaf removes an import-time cycle and makes cold startup
failures local to the service actually requested.
"""
from __future__ import annotations

from importlib import import_module
from types import ModuleType

__all__ = ["integrations", "runtime", "staff", "trust_admin"]
_LAZY_ATTRIBUTE_MODULES = {"integrations", "staff", "trust_admin"}


def __getattr__(name: str) -> ModuleType:
    """Preserve historical direct attributes without pulling in runtime."""

    if name not in _LAZY_ATTRIBUTE_MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return import_module(f"{__name__}.{name}")


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
