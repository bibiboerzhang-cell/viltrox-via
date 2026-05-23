#!/usr/bin/env python3
"""Build the P5.68 read-only X comments go/no-go report."""
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
from app.services.vkpi import x_comments_go_no_go  # noqa: E402


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def render_markdown(report: dict[str, Any]) -> str:
    modes = _as_dict(report.get("provider_modes"))
    targets = _as_dict(report.get("targets"))
    lines = [
        "# V-KPI P5.68 X Comments Go/No-Go",
        "",
        "Read-only X comments validation gate. It does not call X, Apify, or enqueue sync work.",
        "",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Passed: `{str(bool(report.get('passed'))).lower()}`",
        f"- Decision: `{report.get('decision')}`",
        f"- Preferred provider path: `{modes.get('preferred_path')}`",
        f"- Run ready after approval: `{str(bool(report.get('run_ready_after_approval'))).lower()}`",
        f"- Targets: `{targets.get('count')}` / `{_as_dict(report.get('limits')).get('target_count_required')}`",
        f"- Next step: {report.get('next_step')}",
        "",
        "## Stop Rules",
        "",
    ]
    for item in _as_dict(report.get("go_no_go_criteria")).get("stop_rules") or []:
        lines.append(f"- {item}")
    lines.extend(["", "## Checks", ""])
    for key, value in _as_dict(report.get("checks")).items():
        lines.append(f"- `{key}`: `{str(bool(value)).lower()}`")
    if targets.get("errors"):
        lines.extend(["", "## Target Errors", ""])
        for error in targets.get("errors") or []:
            lines.append(f"- `{error}`")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a read-only X comments go/no-go report.")
    parser.add_argument("--targets-file", default="")
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
        report = x_comments_go_no_go.build_x_comments_go_no_go_report(args.targets_file or None)
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
