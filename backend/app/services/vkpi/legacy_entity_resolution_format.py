"""Backwards-compatible legacy entity resolution format shim."""
from __future__ import annotations

import sys

from app.domains.legacy_import import legacy_entity_resolution_format as _legacy_entity_resolution_format

sys.modules[__name__] = _legacy_entity_resolution_format
