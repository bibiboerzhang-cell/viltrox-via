"""Backwards-compat shim. Real implementation lives in the intelligence domain."""

from app.domains.intelligence.today_signals_use_case import *  # noqa: F401,F403
from app.domains.intelligence.today_signals_use_case import build_today_new_signals_v0
