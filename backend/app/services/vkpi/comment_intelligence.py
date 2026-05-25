"""Backwards-compatible module alias for the comment intelligence domain."""
from __future__ import annotations

import sys

from app.domains.comments import intelligence as _impl

sys.modules[__name__] = _impl
