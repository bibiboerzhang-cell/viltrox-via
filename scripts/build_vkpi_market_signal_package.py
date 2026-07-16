#!/usr/bin/env python3
"""Build a no-write market signal package from a provider smoke report."""
from __future__ import annotations
from stdout_utils import out as stdout_out

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from app.domains.market.signal_write_package import build_market_signal_write_package_from_file


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _render_markdown(package: dict) -> str:
    lines = [
        "# Market Signal Write Package v0",
        "",
        f"- generated_at: `{package['generated_at']}`",
        f"- write_db: `{package['write_db']}`",
        f"- passed: `{package['passed']}`",
        f"- target_tables: `{', '.join(package['target_tables'])}`",
        "",
        "## Summary",
        "",
        "| metric | value |",
        "|---|---:|",
    ]
    for key, value in package["summary"].items():
        lines.append(f"| {key} | {value} |")
    lines.extend(["", "## Checks", "", "| check | value |", "|---|---:|"])
    for key, value in package["checks"].items():
        lines.append(f"| {key} | {value} |")
    lines.extend(["", "## Scan Run Preview", ""])
    scan = package["scan_run"]
    for key in ("run_uid", "scan_type", "status", "provider_status", "completed_at"):
        lines.append(f"- `{key}`: `{scan.get(key)}`")
    lines.extend(["", "## Source Preview", ""])
    for source in package["sources"][:10]:
        metadata = source.get("metadata_json") if isinstance(source.get("metadata_json"), dict) else {}
        descriptor = metadata.get("subreddit") or metadata.get("source_key") or source.get("platform") or "-"
        lines.append(
            f"- `{source['source_temp_uid']}` · `{source.get('source_type')}` · {descriptor} · "
            f"{source['title']} · {source['source_url']}"
        )
    lines.extend(["", "## Mention Preview", ""])
    for mention in package["mentions"][:10]:
        lines.append(
            f"- score `{mention['score']}` · product `{mention['product_sku'] or '-'}` · "
            f"competitor `{mention['competitor_product'] or '-'}` · {mention['mention_text']}"
        )
    lines.extend(
        [
            "",
            "## Policy",
            "",
            "- This package is dry-run only.",
            "- Backup before the first DB write.",
            "- Insert raw market mentions before promoting reviewed rows to competitor signals.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build V-KPI market signal no-write package.")
    parser.add_argument("report_json")
    parser.add_argument("--out-dir", default="runtime/ops")
    args = parser.parse_args()

    package = build_market_signal_write_package_from_file(args.report_json)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{_stamp()}-market-signal-write-package-v0"
    json_path = out_dir / f"{prefix}.json"
    md_path = out_dir / f"{prefix}.md"
    json_path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(package), encoding="utf-8")
    stdout_out(
        json.dumps(
            {
                "json_path": str(json_path.resolve()),
                "md_path": str(md_path.resolve()),
                "summary": package["summary"],
                "passed": package["passed"],
                "checks": package["checks"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
