from __future__ import annotations

import ast
from pathlib import Path

from scripts.vkpi_engineering_health_collect import collect_complexity


ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    ROOT / "backend/app/domains/market_brain/outreach_reply_truth.py",
    ROOT
    / "backend/app/domains/market_brain/outreach_reply_receipt_validation.py",
)


def test_outreach_receipt_validation_stays_below_the_v1_redlines() -> None:
    rows = []
    for source_path in SOURCES:
        source = source_path.read_text(encoding="utf-8")
        rows.extend(collect_complexity({str(source_path): ast.parse(source)}))
        assert len(source.splitlines()) <= 800
    receipt = next(
        row for row in rows if row.qualified_name == "verified_receipt_for_binding"
    )

    assert receipt.cc <= 10
    assert max(row.cc for row in rows) <= 50
