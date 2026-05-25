"""Backwards-compatible legacy import audit shim."""
from __future__ import annotations

import sys

from app.domains.legacy_import import legacy_import_audit as _legacy_import_audit

sys.modules[__name__] = _legacy_import_audit
