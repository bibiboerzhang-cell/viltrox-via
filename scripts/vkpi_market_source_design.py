#!/usr/bin/env python3
"""Build the P5.66 read-only market signal source design report."""
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
from app.domains.market import source_design_use_case as market_source_design  # noqa: E402


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def render_markdown(report: dict[str, Any]) -> str:
    summary = _as_dict(report.get("summary"))
    lines = [
        "# V-KPI P5.66 Market Signal Source Design",
        "",
        "Read-only source design report. It defines market-signal source contracts and gates without crawling Reddit, X, RSS, competitor sites, or YouTube search.",
        "",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Passed: `{str(bool(report.get('passed'))).lower()}`",
        f"- Source count: `{summary.get('source_count')}`",
        f"- Design-only sources: `{summary.get('design_only_sources')}`",
        f"- Blocked sources: `{summary.get('blocked_sources')}`",
        f"- Tables present: `{summary.get('tables_present')}`",
        "",
        "## Sources",
        "",
    ]
    for source in report.get("sources") or []:
        if not isinstance(source, dict):
            continue
        lines.append(
            f"- `{source.get('source_key')}`: `{source.get('recommended_path')}` / gate `{source.get('execution_gate')}` / collect_now `{str(bool(source.get('can_collect_now'))).lower()}`"
        )
    lines.extend(["", "## Next Gates", ""])
    for gate in report.get("next_gates") or []:
        if isinstance(gate, dict):
            lines.append(f"- `{gate.get('phase')}`: {gate.get('task')} - {gate.get('decision')}")
    lines.extend(["", "## Checks", ""])
    for key, value in _as_dict(report.get("checks")).items():
        lines.append(f"- `{key}`: `{str(bool(value)).lower()}`")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a read-only market signal source design report.")
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
        report = market_source_design.build_market_source_design_report()
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
