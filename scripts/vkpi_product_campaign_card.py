#!/usr/bin/env python3
"""Build the P5.70 read-only product campaign card."""
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
from app.domains.products import product_campaign_card  # noqa: E402


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def render_markdown(report: dict[str, Any]) -> str:
    product = _as_dict(report.get("product"))
    summary = _as_dict(report.get("summary"))
    lines = [
        "# V-KPI P5.70 Product Campaign Card",
        "",
        "Read-only campaign planning card. It does not create projects, call providers, or run sync.",
        "",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Passed: `{str(bool(report.get('passed'))).lower()}`",
        f"- SKU: `{product.get('sku')}`",
        f"- Mount: `{product.get('mount')}`",
        f"- Focal: `{product.get('focal_length_label')}`",
        f"- Aperture: `{product.get('max_aperture_label')}`",
        f"- KOL candidates: `{summary.get('kol_candidates', 0)}`",
        f"- Market risk tier: `{summary.get('market_risk_tier')}`",
        "",
        "## KOL Candidates",
        "",
    ]
    for item in report.get("kol_candidates") or []:
        lines.append(
            f"- `{item.get('platform')}` @{item.get('handle')} score=`{item.get('score')}` "
            f"confidence=`{item.get('confidence')}` followers=`{item.get('followers')}`"
        )
    lines.extend(["", "## Actions", ""])
    for action in report.get("campaign_actions") or []:
        lines.append(f"- {action}")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a read-only product campaign card.")
    parser.add_argument("--sku", default="")
    parser.add_argument("--kol-limit", type=int, default=200)
    parser.add_argument("--top-kols", type=int, default=12)
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
        report = product_campaign_card.build_product_campaign_card(
            sku=args.sku,
            kol_limit=args.kol_limit,
            top_kols=args.top_kols,
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
