"""Backwards-compatible core schema shim."""
from __future__ import annotations

import sys

from app.platform.db import schema as _schema

sys.modules[__name__] = _schema
