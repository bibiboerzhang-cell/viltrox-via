#!/usr/bin/env python3
"""Score an existing V-KPI market LLM smoke report without provider calls."""
from __future__ import annotations
from stdout_utils import out as stdout_out

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.market.llm_quality import evaluate_market_llm_report_file  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Score a V-KPI market LLM smoke report quality gate.")
    parser.add_argument("report_json")
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()

    result = evaluate_market_llm_report_file(args.report_json)
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    stdout_out(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("usable_for_ui") else 3


if __name__ == "__main__":
    raise SystemExit(main())
