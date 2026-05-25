"""Backwards-compatible legacy import staging shim."""
from __future__ import annotations

import sys

from app.domains.legacy_import import legacy_import_staging as _legacy_import_staging

sys.modules[__name__] = _legacy_import_staging
