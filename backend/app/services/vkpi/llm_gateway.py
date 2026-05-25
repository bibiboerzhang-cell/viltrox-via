"""Backwards-compatible module alias for the platform LLM gateway."""
from __future__ import annotations

import importlib
import sys

_impl = importlib.import_module("app.platform.llm_gateway")

sys.modules[__name__] = _impl
