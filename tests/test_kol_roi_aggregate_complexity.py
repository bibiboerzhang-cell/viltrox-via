from __future__ import annotations

import ast
from pathlib import Path

from scripts.vkpi_engineering_health_collect import collect_complexity


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "backend/app/domains/kol/roi_aggregate.py"


def test_bulk_roi_projection_stays_below_the_v1_complexity_redline() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    rows = collect_complexity({str(SOURCE): ast.parse(source)})
    bulk = next(row for row in rows if row.qualified_name == "_bulk_high_value_signals")

    assert bulk.cc <= 15
    assert max(row.cc for row in rows) <= 30
    assert len(source.splitlines()) < 800
