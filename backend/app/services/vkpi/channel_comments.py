"""Backwards-compatible module alias for channel comment helpers."""
from __future__ import annotations

import sys

from app.domains.comments import channel as _impl

sys.modules[__name__] = _impl
