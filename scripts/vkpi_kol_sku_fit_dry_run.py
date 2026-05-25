#!/usr/bin/env python3
"""Build the P5.64 read-only KOL x SKU Product Fit dry-run report."""
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
from app.domains.kol import sku_fit as kol_sku_fit  # noqa: E402


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def render_markdown(report: dict[str, Any]) -> str:
    summary = _as_dict(report.get("summary"))
    kol = _as_dict(report.get("kol"))
    lines = [
        "# V-KPI P5.64 KOL x SKU Fit Dry Run",
        "",
        "Read-only rule report. It uses existing KOL data, `vkpi_product_aliases`, and `vkpi_product_spec_facts`; it does not write scores or call providers.",
        "",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Passed: `{str(bool(report.get('passed'))).lower()}`",
        f"- KOL: `{kol.get('platform')}/{kol.get('handle')}` (`{kol.get('id')}`)",
        f"- SKU facts: `{summary.get('sku_fact_count')}`",
        f"- Scored SKUs: `{summary.get('scored_sku_count')}`",
        f"- Top count: `{summary.get('top_count')}`",
        "",
        "## Top SKUs",
        "",
    ]
    for item in report.get("top_skus") or []:
        if not isinstance(item, dict):
            continue
        breakdown = _as_dict(item.get("score_breakdown"))
        lines.append(
            f"- `{item.get('sku')}` score `{item.get('score')}` confidence `{item.get('confidence')}` "
            f"(alias `{breakdown.get('alias')}`, spec `{breakdown.get('spec')}`, 11D `{breakdown.get('dimensions11')}`)"
        )
    lines.extend(["", "## Checks", ""])
    for key, value in _as_dict(report.get("checks")).items():
        lines.append(f"- `{key}`: `{str(bool(value)).lower()}`")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a read-only KOL x SKU Product Fit report.")
    parser.add_argument("--kol-pool-id", type=int, default=0)
    parser.add_argument("--query", default="viltrox")
    parser.add_argument("--sku-limit", type=int, default=500)
    parser.add_argument("--top-n", type=int, default=12)
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
        report = kol_sku_fit.build_kol_sku_fit_report(
            kol_pool_id=int(args.kol_pool_id or 0),
            query=str(args.query or "viltrox"),
            sku_limit=max(1, int(args.sku_limit or 500)),
            top_n=max(1, int(args.top_n or 12)),
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
