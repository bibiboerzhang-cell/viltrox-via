"""Backwards-compatible module alias for daily sync orchestration."""

from __future__ import annotations

import sys

from app.domains.sync import daily_sync as _daily_sync

sys.modules[__name__] = _daily_sync

