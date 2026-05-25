"""Backwards-compatible product and industry schema shim."""
from __future__ import annotations

import sys

from app.platform.db import schema_product_industry as _schema_product_industry

sys.modules[__name__] = _schema_product_industry
