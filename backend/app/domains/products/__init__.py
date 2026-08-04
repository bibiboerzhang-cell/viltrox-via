"""Products domain facade with dependency-safe lazy submodule loading.

Importing one leaf helper, such as ``product_aliases.normalize_alias``, must not
initialize campaign, model or scraping dependencies from sibling modules.
"""

from __future__ import annotations

from importlib import import_module
from types import ModuleType


__all__ = ["product_aliases", "product_campaign_card", "product_fit_monitor", "product_specs"]


def __getattr__(name: str) -> ModuleType:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(f"{__name__}.{name}")
    globals()[name] = module
    return module
