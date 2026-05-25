"""Backwards-compatible P5 selected schema shim."""
from __future__ import annotations

import sys

from app.platform.db import schema_p5_selected as _schema_p5_selected

sys.modules[__name__] = _schema_p5_selected
