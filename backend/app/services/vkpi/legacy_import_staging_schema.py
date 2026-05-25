"""Backwards-compatible legacy import staging schema shim."""
from __future__ import annotations

import sys

from app.domains.legacy_import import legacy_import_staging_schema as _legacy_import_staging_schema

sys.modules[__name__] = _legacy_import_staging_schema
