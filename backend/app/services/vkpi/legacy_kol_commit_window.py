"""Backwards-compatible legacy KOL commit window shim."""
from __future__ import annotations

import sys

from app.domains.legacy_import import legacy_kol_commit_window as _legacy_kol_commit_window

sys.modules[__name__] = _legacy_kol_commit_window
