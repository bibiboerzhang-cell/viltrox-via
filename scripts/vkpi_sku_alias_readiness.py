#!/usr/bin/env python3
"""Build the P5.62 SKU alias readiness report."""
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
from app.domains.products import product_aliases  # noqa: E402


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def render_markdown(report: dict[str, Any]) -> str:
    summary = _as_dict(report.get("summary"))
    launch_probe = _as_dict(report.get("launch_probe"))
    lines = [
        "# V-KPI P5.62 SKU Alias Readiness",
        "",
        "Readiness report for the official product alias layer. It uses existing `vkpi_products` rows and does not call providers, trigger sync, or run LLM/Gemini.",
        "",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Passed: `{str(bool(report.get('passed'))).lower()}`",
        f"- Apply mode: `{str(bool(report.get('apply'))).lower()}`",
        f"- Ensure schema: `{str(bool(report.get('ensure_schema'))).lower()}`",
        f"- Product count: `{summary.get('product_count')}`",
        f"- Generated aliases: `{summary.get('generated_alias_count')}`",
        f"- Aliases written: `{summary.get('aliases_written')}`",
        f"- Ambiguous alias norms: `{summary.get('ambiguous_alias_norm_count')}`",
        "",
        "## Alias Types",
        "",
    ]
    for key, value in _as_dict(report.get("alias_type_counts")).items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Launch Probe", ""])
    if launch_probe.get("available"):
        lines.append(f"- Launch rows: `{launch_probe.get('launch_count')}`")
        lines.append(f"- Exact alias hits: `{launch_probe.get('exact_alias_hits')}`")
        lines.append(f"- Misses: `{launch_probe.get('misses')}`")
    else:
        lines.append(f"- Status: `{launch_probe.get('reason', 'not_available')}`")
    lines.extend(["", "## Checks", ""])
    for key, value in _as_dict(report.get("checks")).items():
        lines.append(f"- `{key}`: `{str(bool(value)).lower()}`")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a SKU alias readiness report.")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--ensure-schema", action="store_true", help="Create the alias table if it is missing.")
    parser.add_argument("--apply", action="store_true", help="Upsert generated aliases into vkpi_product_aliases.")
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
        report = product_aliases.build_alias_readiness_report(
            limit=max(1, int(args.limit or 500)),
            apply=bool(args.apply),
            ensure_schema=bool(args.ensure_schema),
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
