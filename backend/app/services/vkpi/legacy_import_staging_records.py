"""Backwards-compatible legacy import staging records shim."""
from __future__ import annotations

import sys

from app.domains.legacy_import import legacy_import_staging_records as _legacy_import_staging_records

sys.modules[__name__] = _legacy_import_staging_records
