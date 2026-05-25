"""Backwards-compat shim. Real implementation lives in the intelligence domain."""

from app.domains.intelligence.brain_acceptance_use_case import *  # noqa: F401,F403
from app.domains.intelligence.brain_acceptance_use_case import build_brain_layer_acceptance_v0
