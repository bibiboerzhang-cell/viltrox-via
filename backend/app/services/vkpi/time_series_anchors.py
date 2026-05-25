"""Backwards-compatible time-series anchor shim."""
from __future__ import annotations

import sys

from app.domains.analytics import time_series_anchors as _time_series_anchors

sys.modules[__name__] = _time_series_anchors
