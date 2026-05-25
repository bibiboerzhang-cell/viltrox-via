"""Backwards-compatible audit schema shim."""
from __future__ import annotations

import sys

from app.platform.db import schema_audit as _schema_audit

sys.modules[__name__] = _schema_audit
