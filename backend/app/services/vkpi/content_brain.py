"""Backwards-compatible module alias for market content brain."""
from __future__ import annotations

import importlib
import sys

_impl = importlib.import_module("app.domains.market.content_brain")

sys.modules[__name__] = _impl
