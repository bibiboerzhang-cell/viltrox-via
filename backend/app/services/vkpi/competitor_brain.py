"""Backwards-compatible module alias for market competitor brain."""
from __future__ import annotations

import importlib
import sys

_impl = importlib.import_module("app.domains.market.competitor_brain")

sys.modules[__name__] = _impl
