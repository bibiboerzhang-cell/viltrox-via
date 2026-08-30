from __future__ import annotations

import ast
from pathlib import Path

from scripts.vkpi_engineering_health_collect import collect_complexity


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "backend/app/domains/commerce/dealer_coverage_repository.py"


def test_dealer_coverage_projection_stays_below_the_v1_complexity_redline() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    rows = collect_complexity({str(SOURCE): ast.parse(source)})
    facade = next(row for row in rows if row.qualified_name == "dealer_coverage_summary")

    assert facade.cc <= 15
    assert max(row.cc for row in rows) <= 30
    assert len(source.splitlines()) < 800
