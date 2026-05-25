"""Backwards-compatible module alias for settings platform crawl controls."""
from __future__ import annotations

import sys
import importlib

_impl = importlib.import_module("app.domains.settings.platform_crawl")

sys.modules[__name__] = _impl
