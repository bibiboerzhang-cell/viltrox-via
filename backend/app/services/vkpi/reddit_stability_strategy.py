"""Backwards-compatible Reddit stability strategy shim."""
from __future__ import annotations

import sys

from app.domains.market import reddit_stability_strategy as _reddit_stability_strategy

sys.modules[__name__] = _reddit_stability_strategy
