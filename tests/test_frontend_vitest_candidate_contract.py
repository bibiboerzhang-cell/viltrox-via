from __future__ import annotations

import re
from pathlib import Path


VITEST_CONFIG = Path(__file__).resolve().parents[1] / "frontend" / "vitest.config.ts"


def test_vitest_uses_numeric_loopback_without_enabling_an_api_listener() -> None:
    """The no-network candidate gate must not make Vite resolve localhost."""

    source = VITEST_CONFIG.read_text(encoding="utf-8")

    assert re.search(
        r"server\s*:\s*\{\s*host\s*:\s*[\"']127\.0\.0\.1[\"']\s*,?\s*\}",
        source,
    )
    assert not re.search(r"test\s*:\s*\{[^}]*\bapi\s*:", source, re.DOTALL)
