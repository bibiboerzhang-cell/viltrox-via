"""Backwards-compatible legacy entity resolution decisions shim."""
from __future__ import annotations

import sys

from app.domains.legacy_import import legacy_entity_resolution_decisions as _legacy_entity_resolution_decisions

sys.modules[__name__] = _legacy_entity_resolution_decisions
