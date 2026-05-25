#!/usr/bin/env python3
"""Build the P5.69 read-only market intelligence v0 report."""
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
from app.domains.market import intelligence_use_case as market_intelligence_v0  # noqa: E402


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def render_markdown(report: dict[str, Any]) -> str:
    summary = _as_dict(report.get("summary"))
    lines = [
        "# V-KPI P5.69 Market Intelligence v0",
        "",
        "Read-only market intelligence report from existing V-KPI signal tables. No external source is fetched.",
        "",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Passed: `{str(bool(report.get('passed'))).lower()}`",
        f"- Status: `{report.get('status')}`",
        f"- Signals loaded: `{summary.get('signals_loaded', 0)}`",
        f"- Launch candidates: `{summary.get('launch_candidates', 0)}`",
        f"- Comment opportunities: `{summary.get('comment_opportunities', 0)}`",
        f"- High priority: `{summary.get('high_priority', 0)}`",
        "",
        "## Hot Brands",
        "",
    ]
    for item in report.get("hot_brands") or []:
        lines.append(f"- `{item.get('brand')}` count=`{item.get('count')}` score=`{item.get('score')}`")
    lines.extend(["", "## Hot Topics", ""])
    for item in report.get("hot_topics") or []:
        lines.append(f"- `{item.get('signal_type')}` count=`{item.get('count')}`")
    lines.extend(["", "## Checks", ""])
    for key, value in _as_dict(report.get("checks")).items():
        lines.append(f"- `{key}`: `{str(bool(value)).lower()}`")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a read-only market intelligence v0 report.")
    parser.add_argument("--limit", type=int, default=120)
    parser.add_argument("--run-id", type=int)
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
        report = market_intelligence_v0.build_market_intelligence_v0(limit=args.limit, run_id=args.run_id)
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
