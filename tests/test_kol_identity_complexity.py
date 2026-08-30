from __future__ import annotations

import ast
from pathlib import Path

from scripts.vkpi_engineering_health_collect import collect_complexity


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "backend/app/domains/kol/identity.py"


def test_url_identity_projection_stays_below_the_v1_complexity_redline() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    rows = collect_complexity({str(SOURCE): ast.parse(source)})
    aliases = next(row for row in rows if row.qualified_name == "_url_identity_aliases")

    assert aliases.cc <= 25
    assert max(row.cc for row in rows) <= 30
    assert len(source.splitlines()) < 800
