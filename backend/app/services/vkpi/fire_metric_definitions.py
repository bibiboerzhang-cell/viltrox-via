"""Backwards-compatible fire metric definitions shim."""
from __future__ import annotations

import sys

from app.domains.analytics import fire_metric_definitions as _fire_metric_definitions

sys.modules[__name__] = _fire_metric_definitions
