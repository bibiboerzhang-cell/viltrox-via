"""Backwards-compatible module alias for comment-intelligence rules."""
from __future__ import annotations

import sys

from app.domains.comments import intelligence_rules as _impl

sys.modules[__name__] = _impl
