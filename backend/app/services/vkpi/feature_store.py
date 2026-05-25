"""Backwards-compatible feature store shim."""
from __future__ import annotations

import sys

from app.domains.recommendations import feature_store as _feature_store

sys.modules[__name__] = _feature_store
