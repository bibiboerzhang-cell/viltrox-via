from __future__ import annotations

import ast
from pathlib import Path

from scripts.vkpi_engineering_health_collect import collect_complexity


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "backend/app/domains/kol/profile_basics.py"


def test_profile_basics_write_stays_below_the_v1_hard_redline() -> None:
    rows = collect_complexity({str(SOURCE): ast.parse(SOURCE.read_text(encoding="utf-8"))})
    write = next(row for row in rows if row.qualified_name == "write_kol_profile_basics")

    assert write.cc <= 50
    assert max(row.cc for row in rows) <= 50
