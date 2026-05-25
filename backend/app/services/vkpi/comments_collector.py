"""Backwards-compatible module alias for the comments collector domain."""
from __future__ import annotations

import sys

from app.domains.comments import collector as _impl

sys.modules[__name__] = _impl
