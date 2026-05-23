#!/usr/bin/env python3
"""Build the P5.65 read-only Product Fit monitor report."""
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
from app.services.vkpi import product_fit_monitor  # noqa: E402


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def render_markdown(report: dict[str, Any]) -> str:
    coverage = _as_dict(report.get("coverage"))
    sample = _as_dict(report.get("sample_kol_sku_fit"))
    lines = [
        "# V-KPI P5.65 Product Fit Monitor",
        "",
        "Read-only monitor for Product Fit alias/spec coverage, join misses, ambiguity, low confidence specs, and one KOL x SKU dry-run sample.",
        "",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Passed: `{str(bool(report.get('passed'))).lower()}`",
        f"- Status: `{report.get('status')}`",
        f"- Products: `{coverage.get('product_count')}`",
        f"- Alias coverage: `{coverage.get('alias_sku_coverage')}`",
        f"- Spec coverage: `{coverage.get('spec_sku_coverage')}`",
        f"- Sample KOL: `{_as_dict(sample.get('kol')).get('platform')}/{_as_dict(sample.get('kol')).get('handle')}`",
        "",
        "## Warnings",
        "",
    ]
    warnings = report.get("warnings") or []
    if not warnings:
        lines.append("- none")
    else:
        for warning in warnings:
            if isinstance(warning, dict):
                lines.append(f"- `{warning.get('type')}`: `{warning.get('count')}`")
    lines.extend(["", "## Checks", ""])
    for key, value in _as_dict(report.get("checks")).items():
        lines.append(f"- `{key}`: `{str(bool(value)).lower()}`")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a read-only Product Fit monitor report.")
    parser.add_argument("--query", default="viltrox")
    parser.add_argument("--sample-kol-pool-id", type=int, default=0)
    parser.add_argument("--low-threshold", type=float, default=70.0)
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
        report = product_fit_monitor.build_monitor_report(
            query=str(args.query or "viltrox"),
            sample_kol_pool_id=int(args.sample_kol_pool_id or 0),
            low_threshold=float(args.low_threshold or 70.0),
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
