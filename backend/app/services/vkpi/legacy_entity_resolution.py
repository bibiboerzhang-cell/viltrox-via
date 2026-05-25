"""Backwards-compatible legacy entity resolution shim."""
from __future__ import annotations

import sys

from app.domains.legacy_import import legacy_entity_resolution as _legacy_entity_resolution

sys.modules[__name__] = _legacy_entity_resolution
