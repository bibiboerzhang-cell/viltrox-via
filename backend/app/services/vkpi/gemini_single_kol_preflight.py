"""Backwards-compatible module alias for Gemini single-KOL preflight."""
from __future__ import annotations

import importlib
import sys

_impl = importlib.import_module("app.domains.intelligence.gemini_single_kol_preflight")

sys.modules[__name__] = _impl
