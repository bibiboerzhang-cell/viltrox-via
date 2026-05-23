#!/usr/bin/env python3
"""Build the P6.77 read-only weekly action plan report."""
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
from app.services.vkpi import weekly_action_plan_v0  # noqa: E402


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def render_markdown(report: dict[str, Any]) -> str:
    summary = _as_dict(report.get("summary"))
    lines = [
        "# V-KPI P6.77 Weekly Action Plan v0",
        "",
        "Read-only weekly planning checklist from launch acceptance and today's signals.",
        "",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Passed: `{str(bool(report.get('passed'))).lower()}`",
        f"- Plan version: `{report.get('plan_version')}`",
        f"- SKU: `{summary.get('sku')}`",
        f"- Actions: `{summary.get('action_count')}`",
        f"- Today abnormal growth: `{summary.get('today_abnormal_growth')}`",
        f"- Comment status: `{summary.get('today_comment_status')}`",
        "",
        "## Actions",
        "",
    ]
    for item in report.get("actions") or []:
        lines.append(
            f"- `{item.get('priority')}` `{item.get('action_type')}` score=`{item.get('score')}`: {item.get('title')}"
        )
    lines.extend(["", "## Checks", ""])
    for key, value in _as_dict(report.get("checks")).items():
        lines.append(f"- `{key}`: `{str(bool(value)).lower()}`")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build weekly action plan v0 report.")
    parser.add_argument("--sku", default="")
    parser.add_argument("--top-n", type=int, default=12)
    parser.add_argument("--lookback-hours", type=int, default=24)
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
        report = weekly_action_plan_v0.build_weekly_action_plan_v0(
            sku=args.sku,
            top_n=args.top_n,
            lookback_hours=args.lookback_hours,
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
