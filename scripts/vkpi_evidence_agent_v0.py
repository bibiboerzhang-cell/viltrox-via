#!/usr/bin/env python3
"""Build the P7.81 read-only Evidence Agent report."""
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
from app.domains.intelligence import evidence_agent_use_case as evidence_agent_v0  # noqa: E402


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def render_markdown(report: dict[str, Any]) -> str:
    summary = _as_dict(report.get("summary"))
    lines = [
        "# V-KPI P7.81 Evidence Agent v0",
        "",
        "Read-only evidence-chain organizer. It summarizes existing evidence only.",
        "",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Passed: `{str(bool(report.get('passed'))).lower()}`",
        f"- Agent status: `{summary.get('agent_status')}`",
        f"- Target source: `{summary.get('target_source')}`",
        f"- Targets: `{summary.get('target_count')}`",
        f"- Chains: `{summary.get('chain_count')}`",
        f"- Evidence refs: `{summary.get('evidence_ref_count')}`",
        f"- Claims: `{summary.get('claim_count')}`",
        f"- Errors: `{summary.get('error_count')}`",
        "",
        "## Chains",
        "",
    ]
    for chain in report.get("chains") or []:
        item = _as_dict(chain.get("item"))
        lines.append(
            f"- kol_pool_id=`{chain.get('kol_pool_id')}` @{item.get('handle') or '-'} "
            f"status=`{chain.get('status')}` refs=`{chain.get('evidence_ref_count')}` "
            f"claims=`{len(chain.get('claims') or [])}`"
        )
    if not report.get("chains"):
        lines.append("- none")
    lines.extend(["", "## Checks", ""])
    for key, value in _as_dict(report.get("checks")).items():
        lines.append(f"- `{key}`: `{str(bool(value)).lower()}`")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Evidence Agent v0 report.")
    parser.add_argument("--kol-pool-ids", default="", help="Comma-separated kol_pool IDs. If omitted, reads P6.77 weekly actions.")
    parser.add_argument("--ops-dir", default="runtime/ops")
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--ref-limit", type=int, default=24)
    parser.add_argument("--claim-limit", type=int, default=12)
    parser.add_argument("--skip-product-fit", action="store_true")
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
        report = evidence_agent_v0.build_evidence_agent_v0(
            kol_pool_ids=args.kol_pool_ids,
            ops_dir=args.ops_dir,
            limit=args.limit,
            ref_limit=args.ref_limit,
            claim_limit=args.claim_limit,
            include_product_fit=not bool(args.skip_product_fit),
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
