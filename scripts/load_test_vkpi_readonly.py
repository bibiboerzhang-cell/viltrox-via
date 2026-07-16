#!/usr/bin/env python3
"""Compatibility entry point for the modular V-KPI read-only load runner."""
from __future__ import annotations

import sys
from pathlib import Path


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ops import load_test_cli as _implementation  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(_implementation.main())

# Preserve the historical import surface, including private test hooks.  Using
# the implementation module object itself also keeps monkeypatching semantics
# compatible for callers that patch globals such as ``environment_snapshot``.
sys.modules[__name__] = _implementation
