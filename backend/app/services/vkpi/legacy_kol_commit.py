"""Backwards-compatible legacy KOL commit shim."""
from __future__ import annotations

import sys

from app.domains.legacy_import import legacy_kol_commit as _legacy_kol_commit

sys.modules[__name__] = _legacy_kol_commit
