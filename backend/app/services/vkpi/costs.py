"""Compatibility shim for the costs domain."""
from __future__ import annotations

from app.domains.costs import ledger as _ledger
from app.domains.costs import common as _common
from app.domains.costs import product_catalog as _product_catalog

for _module in (_common, _product_catalog, _ledger):
    for _name in dir(_module):
        if not _name.startswith("__"):
            globals()[_name] = getattr(_module, _name)

del _module, _name
__all__ = [name for name in globals() if not name.startswith("__") and name not in {"_ledger", "_common", "_product_catalog"}]
