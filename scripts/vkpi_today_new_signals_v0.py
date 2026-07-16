#!/usr/bin/env python3
"""Build the P6.76 read-only today new signals report."""
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
from app.domains.intelligence import today_signals_use_case as today_new_signals_v0  # noqa: E402


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def render_markdown(report: dict[str, Any]) -> str:
    summary = _as_dict(report.get("summary"))
    lines = [
        "# V-KPI P6.76 Today New Signals v0",
        "",
        "Read-only 24h digest of growth, market, and cached comment signals.",
        "",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Passed: `{str(bool(report.get('passed'))).lower()}`",
        f"- Digest version: `{report.get('digest_version')}`",
        f"- Trend signals: `{summary.get('trend_signals_24h')}`",
        f"- Abnormal growth: `{summary.get('abnormal_growth_24h')}`",
        f"- Market events: `{summary.get('market_events_24h')}`",
        f"- Cached comments: `{summary.get('cached_comments_24h')}`",
        f"- Comment status: `{summary.get('comment_status')}`",
        f"- Action items: `{summary.get('action_items')}`",
        "",
        "## Action Items",
        "",
    ]
    for item in report.get("action_items") or []:
        lines.append(f"- `{item.get('priority')}` `{item.get('action')}`: {item.get('reason')}")
    lines.extend(["", "## Checks", ""])
    for key, value in _as_dict(report.get("checks")).items():
        lines.append(f"- `{key}`: `{str(bool(value)).lower()}`")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build today new signals v0 report.")
    parser.add_argument("--lookback-hours", type=int, default=24)
    parser.add_argument("--limit", type=int, default=100)
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
        report = today_new_signals_v0.build_today_new_signals_v0(
            lookback_hours=args.lookback_hours,
            limit=args.limit,
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
