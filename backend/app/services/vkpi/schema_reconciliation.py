"""Backwards-compatible reconciliation schema shim."""
from __future__ import annotations

import sys

from app.platform.db import schema_reconciliation as _schema_reconciliation

sys.modules[__name__] = _schema_reconciliation
