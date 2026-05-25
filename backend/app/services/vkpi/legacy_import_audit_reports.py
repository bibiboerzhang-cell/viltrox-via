"""Backwards-compatible legacy import audit reports shim."""
from __future__ import annotations

import sys

from app.domains.legacy_import import legacy_import_audit_reports as _legacy_import_audit_reports

sys.modules[__name__] = _legacy_import_audit_reports
