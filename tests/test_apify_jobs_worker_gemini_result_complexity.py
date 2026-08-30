from __future__ import annotations

import ast
from pathlib import Path

from scripts.vkpi_engineering_health_collect import collect_complexity


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "backend/app/workers/apify_jobs_worker_gemini_result.py"


def test_gemini_result_shaping_stays_below_the_v1_complexity_redline() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    rows = collect_complexity({str(SOURCE): ast.parse(source)})
    metadata = next(row for row in rows if row.qualified_name == "llm_execution_metadata")

    assert metadata.cc <= 30
    assert max(row.cc for row in rows) <= 50
    assert len(source.splitlines()) < 800
