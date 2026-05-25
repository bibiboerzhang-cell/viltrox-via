#!/usr/bin/env python3
"""Controlled first write for a reviewed V-KPI market signal package."""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from app.db.connection import close_db_runtime
from app.services.vkpi.market_signal_ingest import write_market_signal_package


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _render_markdown(result: dict) -> str:
    lines = [
        "# Market Signal First Write Smoke",
        "",
        f"- generated_at: `{result['generated_at']}`",
        f"- backup_ref: `{result['backup_ref']}`",
        f"- run_uid: `{result['run_uid']}`",
        f"- run_id: `{result['run_id']}`",
        f"- write_db: `{result['write_db']}`",
        f"- llm_calls: `{result['llm_calls']}`",
        f"- gemini_calls: `{result['gemini_calls']}`",
        f"- sync_triggered: `{result['sync_triggered']}`",
        "",
        "## Inserted",
        "",
        "| table | rows |",
        "|---|---:|",
    ]
    for table, count in result["inserted"].items():
        lines.append(f"| {table} | {count} |")
    lines.extend(["", "## Counts", "", "| table | before | after |", "|---|---:|---:|"])
    for table, before in result["counts_before"].items():
        lines.append(f"| {table} | {before} | {result['counts_after'].get(table)} |")
    lines.extend(["", "## Checks", "", "| check | value |", "|---|---:|"])
    for check, value in result["checks"].items():
        lines.append(f"| {check} | {value} |")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Wrote only raw market scan/source/mention rows.",
            "- Did not write `vkpi_competitor_signals`.",
            "- Did not call LLM/Gemini.",
            "- Did not trigger sync/deep scan.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Write V-KPI market signal package after backup.")
    parser.add_argument("package_json")
    parser.add_argument("--backup-ref", required=True)
    parser.add_argument("--out-dir", default="runtime/ops")
    parser.add_argument("--yes", action="store_true", help="required to perform the DB write")
    args = parser.parse_args()

    if not args.yes:
        raise SystemExit("--yes is required for controlled DB write")

    package = json.loads(Path(args.package_json).read_text(encoding="utf-8"))
    result = write_market_signal_package(package, backup_ref=args.backup_ref)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{_stamp()}-market-signal-first-write-smoke"
    json_path = out_dir / f"{prefix}.json"
    md_path = out_dir / f"{prefix}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(result), encoding="utf-8")
    print(
        json.dumps(
            {
                "json_path": str(json_path.resolve()),
                "md_path": str(md_path.resolve()),
                "inserted": result["inserted"],
                "counts_before": result["counts_before"],
                "counts_after": result["counts_after"],
                "checks": result["checks"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    asyncio.run(close_db_runtime())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
