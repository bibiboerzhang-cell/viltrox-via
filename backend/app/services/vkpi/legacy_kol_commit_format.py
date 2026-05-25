"""Backwards-compatible legacy KOL commit format shim."""
from __future__ import annotations

import sys

from app.domains.legacy_import import legacy_kol_commit_format as _legacy_kol_commit_format

sys.modules[__name__] = _legacy_kol_commit_format
