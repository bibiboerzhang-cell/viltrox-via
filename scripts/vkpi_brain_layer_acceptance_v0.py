#!/usr/bin/env python3
"""Build the P6.79 read-only brain layer acceptance report."""
from __future__ import annotations
from stdout_utils import out as stdout_out

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

from app.domains.intelligence import brain_acceptance_use_case as brain_layer_acceptance_v0  # noqa: E402


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def render_markdown(report: dict[str, Any]) -> str:
    summary = _as_dict(report.get("summary"))
    lines = [
        "# V-KPI P6.79 Brain Layer Acceptance v0",
        "",
        "Read-only technical acceptance report for the P6.71-P6.78 brain layer.",
        "",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Passed: `{str(bool(report.get('passed'))).lower()}`",
        f"- Technical acceptance: `{str(bool(summary.get('technical_acceptance_passed'))).lower()}`",
        f"- Decision support level: `{summary.get('decision_support_level')}`",
        f"- Can assist decision: `{str(bool(summary.get('can_assist_new_launch_kol_decision'))).lower()}`",
        f"- Business confirmed: `{str(bool(summary.get('business_confirmed'))).lower()}`",
        f"- Official accuracy pending: `{str(bool(summary.get('official_accuracy_pending'))).lower()}`",
        "",
        "## Modules",
        "",
    ]
    for item in report.get("modules") or []:
        lines.append(
            f"- `{item.get('phase')}` {item.get('label')}: loaded=`{str(bool(item.get('loaded'))).lower()}` "
            f"passed=`{str(bool(item.get('passed'))).lower()}` artifact=`{item.get('artifact_name')}`"
        )
    lines.extend(["", "## Open Limits", ""])
    for item in report.get("open_limits") or []:
        lines.append(f"- {item}")
    lines.extend(["", "## Checks", ""])
    for key, value in _as_dict(report.get("checks")).items():
        lines.append(f"- `{key}`: `{str(bool(value)).lower()}`")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build brain layer acceptance v0 report.")
    parser.add_argument("--ops-dir", default="runtime/ops")
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
    report = brain_layer_acceptance_v0.build_brain_layer_acceptance_v0(ops_dir=args.ops_dir)
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


if __name__ == "__main__":
    raise SystemExit(main())
