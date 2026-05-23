#!/usr/bin/env python3
"""Build the P6.72 read-only time-series anchor report."""
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
from app.services.vkpi import time_series_anchors  # noqa: E402


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def render_markdown(report: dict[str, Any]) -> str:
    summary = _as_dict(report.get("summary"))
    lines = [
        "# V-KPI P6.72 Time-Series Anchors",
        "",
        "Read-only anchor contract for replaying trends from existing snapshot and metric tables.",
        "",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Passed: `{str(bool(report.get('passed'))).lower()}`",
        f"- Anchor version: `{report.get('anchor_version')}`",
        f"- Existing anchors: `{summary.get('existing_anchors')}` / `{summary.get('anchor_count')}`",
        f"- Trend replay ready: `{summary.get('trend_replay_ready')}`",
        f"- Delta anchors: `{summary.get('delta_anchors')}`",
        "",
        "## Anchors",
        "",
    ]
    for anchor in report.get("anchors") or []:
        date_range = _as_dict(anchor.get("date_range"))
        lines.append(
            f"- `{anchor.get('anchor_key')}` table=`{anchor.get('table')}` rows=`{anchor.get('row_count')}` "
            f"entities=`{anchor.get('entity_count')}` time=`{anchor.get('primary_time_column')}` "
            f"range=`{date_range.get('min')}`..`{date_range.get('max')}` ready=`{str(bool(anchor.get('trend_replay_ready'))).lower()}`"
        )
    lines.extend(["", "## Checks", ""])
    for key, value in _as_dict(report.get("checks")).items():
        lines.append(f"- `{key}`: `{str(bool(value)).lower()}`")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build time-series anchor report.")
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
        report = time_series_anchors.build_time_series_anchor_report()
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
