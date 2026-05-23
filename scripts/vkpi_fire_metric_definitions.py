#!/usr/bin/env python3
"""Build the P6.71 read-only fire metric definition report."""
from __future__ import annotations

import argparse
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

from app.services.vkpi import fire_metric_definitions  # noqa: E402


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# V-KPI P6.71 Fire Metric Definitions",
        "",
        "Read-only definition contract for what counts as hot before trend detection or forecasting.",
        "",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Passed: `{str(bool(report.get('passed'))).lower()}`",
        f"- Definition version: `{report.get('definition_version')}`",
        "",
        "## Metrics",
        "",
    ]
    for key, item in _as_dict(report.get("metrics")).items():
        lines.append(f"- `{key}`: {item.get('label_zh')} / {item.get('formula')}")
    lines.extend(["", "## Not Allowed", ""])
    for item in _as_dict(report.get("score_contract")).get("not_allowed") or []:
        lines.append(f"- {item}")
    lines.extend(["", "## Checks", ""])
    for key, value in _as_dict(report.get("checks")).items():
        lines.append(f"- `{key}`: `{str(bool(value)).lower()}`")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build fire metric definition report.")
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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = fire_metric_definitions.build_fire_metric_definition_report()
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


if __name__ == "__main__":
    raise SystemExit(main())
