#!/usr/bin/env python3
"""Build the P6.75 read-only prediction calibration report."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.connection import close_db_runtime  # noqa: E402
from app.services.vkpi import prediction_calibration_v0  # noqa: E402


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def render_markdown(report: dict[str, Any]) -> str:
    summary = _as_dict(report.get("summary"))
    lines = [
        "# V-KPI P6.75 Prediction Calibration v0",
        "",
        "Read-only comparison of a saved P6.74 estimate against current trend truth proxies.",
        "",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Passed: `{str(bool(report.get('passed'))).lower()}`",
        f"- Calibration status: `{summary.get('calibration_status')}`",
        f"- Official accuracy: `{str(bool(summary.get('accuracy_official'))).lower()}`",
        f"- Prediction SKU: `{summary.get('prediction_sku')}`",
        f"- Day gap: `{summary.get('day_gap')}`",
        f"- Candidate count: `{summary.get('predicted_candidate_count')}`",
        f"- Hit count: `{summary.get('hit_count')}`",
        f"- Proxy precision: `{summary.get('proxy_precision')}`",
        f"- Platform coverage: `{summary.get('proxy_platform_coverage')}`",
        "",
        "## Candidate Results",
        "",
    ]
    for item in report.get("candidate_results") or []:
        lines.append(
            f"- `{item.get('platform')}` @{item.get('handle')} predicted=`{item.get('predicted_score')}` "
            f"tier=`{item.get('predicted_tier')}` hit=`{str(bool(item.get('hit'))).lower()}` "
            f"truth_abnormal=`{item.get('truth_platform_abnormal_growth')}`"
        )
    lines.extend(["", "## Checks", ""])
    for key, value in _as_dict(report.get("checks")).items():
        lines.append(f"- `{key}`: `{str(bool(value)).lower()}`")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build prediction calibration v0 report.")
    parser.add_argument("--prediction-json", default="")
    parser.add_argument("--truth-json", default="")
    parser.add_argument("--ops-dir", default="runtime/ops")
    parser.add_argument("--top-n", type=int, default=12)
    parser.add_argument("--json-out", default="")
    parser.add_argument("--md-out", default="")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def _write(path_value: str, content: str) -> None:
    if not path_value:
        return
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


async def async_main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        report = prediction_calibration_v0.build_prediction_calibration_v0(
            prediction_json=args.prediction_json,
            truth_json=args.truth_json,
            ops_dir=args.ops_dir,
            top_n=args.top_n,
        )
        markdown = render_markdown(report)
        if args.json_out:
            _write(args.json_out, json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")
        if args.md_out:
            _write(args.md_out, markdown)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        else:
            print(markdown)
        return 0 if report.get("passed") else 3
    finally:
        await close_db_runtime()


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
