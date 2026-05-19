#!/usr/bin/env python3
"""P4-11 project next-action dry-run CLI."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import close_db_runtime  # noqa: E402
from app.services.vkpi.project_next_action import (  # noqa: E402
    FORBIDDEN_WRITE_FLAGS,
    build_project_next_action_preview,
    format_preview_summary,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a P4-11 project next-action dry-run preview.")
    parser.add_argument("--project-id", type=int, default=0, help="Only evaluate one project id")
    parser.add_argument("--stage", default="", help="Only evaluate projects in this stage")
    parser.add_argument("--staff-id", type=int, default=0, help="Filter to assigned/created staff scope")
    parser.add_argument("--priority", default="", choices=["", "high", "medium", "low"], help="Only return this priority")
    parser.add_argument("--include-unassigned", action="store_true", help="Include unassigned projects")
    parser.add_argument("--include-low-evidence", action="store_true", help="Keep rows with fewer than 3 evidence items")
    parser.add_argument("--limit", type=int, default=50, help="Preview item limit, default 50, max 500")
    parser.add_argument("--json-out", default="", help="Write JSON preview to this path")
    parser.add_argument("--md-out", default="", help="Write Markdown report to this path")
    parser.add_argument("--dry-run", action="store_true", default=True, help="P4-11 is always dry-run")
    parser.add_argument(
        "--with-llm-reasons",
        action="store_true",
        help="P4-12: attach budget-gated recommendation reasons to the top next-action suggestions",
    )
    parser.add_argument(
        "--reason-limit",
        type=int,
        default=10,
        help="Maximum number of returned suggestions to enrich with recommendation reasons",
    )
    parser.add_argument("--json", action="store_true", help="Print full JSON payload to stdout")
    return parser.parse_args()


def _reject_forbidden_flags(argv: list[str]) -> None:
    forbidden = FORBIDDEN_WRITE_FLAGS | {"--assign-task", "--transition-stage", "--send-message"}
    used = sorted(forbidden.intersection(argv))
    if used:
        raise ValueError(f"P4-11 dry-run rejects action/provider flags: {', '.join(used)}")


def main() -> int:
    try:
        _reject_forbidden_flags(sys.argv[1:])
        args = parse_args()
        payload = build_project_next_action_preview(
            project_id=args.project_id,
            stage=args.stage,
            staff_id=args.staff_id,
            priority=args.priority,
            include_unassigned=args.include_unassigned,
            include_low_evidence=args.include_low_evidence,
            limit=args.limit,
            json_out=args.json_out,
            md_out=args.md_out,
            with_llm_reasons=args.with_llm_reasons,
            reason_limit=args.reason_limit,
        )
        if args.json:
            print(json.dumps({key: value for key, value in payload.items() if key != "markdown_items"}, ensure_ascii=False, indent=2, default=str))
        else:
            print(format_preview_summary(payload))
            if args.json_out:
                print(f"json_out={args.json_out}")
            if args.md_out:
                print(f"md_out={args.md_out}")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    raise SystemExit(main())
