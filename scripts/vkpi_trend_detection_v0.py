#!/usr/bin/env python3
"""Build the P6.73 read-only trend detection v0 report."""
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
from app.domains.trends import trend_detection_use_case as trend_detection_v0  # noqa: E402


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def render_markdown(report: dict[str, Any]) -> str:
    summary = _as_dict(report.get("summary"))
    lines = [
        "# V-KPI P6.73 Trend Detection v0",
        "",
        "Read-only rule-based trend detection from time-series anchors and market events.",
        "",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Passed: `{str(bool(report.get('passed'))).lower()}`",
        f"- Detection version: `{report.get('detection_version')}`",
        f"- Signals total: `{summary.get('signals_total')}`",
        f"- Abnormal growth signals: `{summary.get('abnormal_growth_signals')}`",
        f"- Market event signals: `{summary.get('market_event_signals')}`",
        f"- Rows loaded: posts=`{summary.get('post_metric_rows_loaded')}`, channels=`{summary.get('channel_metric_rows_loaded')}`, market=`{summary.get('market_signal_rows_loaded')}`",
        "",
        "## Top Signals",
        "",
    ]
    for signal in report.get("signals") or []:
        entity = _as_dict(signal.get("entity"))
        metric = _as_dict(signal.get("metric"))
        label = entity.get("account_handle") or entity.get("brand") or entity.get("post_uid") or entity.get("channel_id")
        lines.append(
            f"- `{signal.get('rule_key')}` severity=`{signal.get('severity')}` score=`{signal.get('score')}` "
            f"confidence=`{signal.get('confidence')}` entity=`{label}` value=`{metric.get('value')}` threshold=`{metric.get('threshold')}`"
        )
    lines.extend(["", "## Checks", ""])
    for key, value in _as_dict(report.get("checks")).items():
        lines.append(f"- `{key}`: `{str(bool(value)).lower()}`")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build trend detection v0 report.")
    parser.add_argument("--lookback-days", type=int, default=14)
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--top-signals", type=int, default=25)
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
        report = trend_detection_v0.build_trend_detection_v0(
            lookback_days=args.lookback_days,
            limit=args.limit,
            top_signals=args.top_signals,
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
