#!/usr/bin/env python3
"""Build the P6.78 read-only prediction accuracy feedback report."""
from __future__ import annotations
from stdout_utils import out as stdout_out

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
from app.services.vkpi import prediction_accuracy_feedback_v0  # noqa: E402


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def render_markdown(report: dict[str, Any]) -> str:
    summary = _as_dict(report.get("summary"))
    lines = [
        "# V-KPI P6.78 Prediction Accuracy Feedback v0",
        "",
        "Read-only feedback report from P6.75 calibration artifacts.",
        "",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Passed: `{str(bool(report.get('passed'))).lower()}`",
        f"- Feedback status: `{summary.get('feedback_status')}`",
        f"- Artifacts: `{summary.get('artifact_count')}`",
        f"- Official runs: `{summary.get('official_runs')}`",
        f"- Smoke runs: `{summary.get('smoke_runs')}`",
        f"- Calibration allowed: `{str(bool(summary.get('calibration_allowed'))).lower()}`",
        f"- Auto tuning allowed: `{str(bool(summary.get('auto_tuning_allowed'))).lower()}`",
        f"- Official proxy precision avg: `{summary.get('official_proxy_precision_avg')}`",
        f"- Smoke proxy precision avg: `{summary.get('smoke_proxy_precision_avg')}`",
        "",
        "## Buckets By SKU",
        "",
    ]
    for sku, item in _as_dict(_as_dict(report.get("buckets")).get("by_sku")).items():
        lines.append(
            f"- `{sku}` runs=`{item.get('runs')}` official=`{item.get('official_runs')}` "
            f"smoke=`{item.get('smoke_runs')}` precision=`{item.get('proxy_precision_avg')}`"
        )
    lines.extend(["", "## Artifacts", ""])
    for item in report.get("calibration_artifacts") or []:
        lines.append(
            f"- `{item.get('artifact_name')}` status=`{item.get('calibration_status')}` "
            f"official=`{str(bool(item.get('accuracy_official'))).lower()}` "
            f"precision=`{item.get('proxy_precision')}` coverage=`{item.get('proxy_platform_coverage')}`"
        )
    lines.extend(["", "## Checks", ""])
    for key, value in _as_dict(report.get("checks")).items():
        lines.append(f"- `{key}`: `{str(bool(value)).lower()}`")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build prediction accuracy feedback v0 report.")
    parser.add_argument("--ops-dir", default="runtime/ops")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--min-official-runs", type=int, default=3)
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
        report = prediction_accuracy_feedback_v0.build_prediction_accuracy_feedback_v0(
            ops_dir=args.ops_dir,
            limit=args.limit,
            min_official_runs=args.min_official_runs,
        )
        markdown = render_markdown(report)
        if args.json_out:
            _write(args.json_out, json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")
        if args.md_out:
            _write(args.md_out, markdown)
        if args.json:
            stdout_out(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        else:
            stdout_out(markdown)
        return 0 if report.get("passed") else 3
    finally:
        await close_db_runtime()


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
