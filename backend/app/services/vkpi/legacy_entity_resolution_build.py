"""Backwards-compatible legacy entity resolution build shim."""
from __future__ import annotations

import sys

from app.domains.legacy_import import legacy_entity_resolution_build as _legacy_entity_resolution_build

sys.modules[__name__] = _legacy_entity_resolution_build
