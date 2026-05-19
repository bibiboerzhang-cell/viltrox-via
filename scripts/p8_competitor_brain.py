#!/usr/bin/env python3
"""P8 deterministic competitor brain preview CLI."""
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
from app.services.vkpi.competitor_brain import (  # noqa: E402
    FORBIDDEN_WRITE_FLAGS,
    apply_competitor_signal_review_suggestions,
    build_competitor_brain_preview,
    build_competitor_signal_review_suggestions,
    commit_competitor_signals,
    format_apply_suggestions,
    format_review_suggestions,
    format_preview_summary,
    review_competitor_signal,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a P8 competitor brain dry-run preview.")
    parser.add_argument("--limit", type=int, default=20, help="Competitor brand limit, default 20, max 200")
    parser.add_argument("--json-out", default="", help="Write JSON preview to this path")
    parser.add_argument("--md-out", default="", help="Write Markdown report to this path")
    parser.add_argument("--dry-run", action="store_true", default=True, help="P8-1 is always dry-run")
    parser.add_argument("--commit-signals", action="store_true", help="P8-3: persist preview signals for review")
    parser.add_argument("--confirm", action="store_true", help="Required with --commit-signals")
    parser.add_argument("--committed-by", default="cli", help="Commit actor label for --commit-signals")
    parser.add_argument("--review-signal", type=int, default=0, help="Review one committed competitor signal by id")
    parser.add_argument("--review-suggestions", action="store_true", help="Print deterministic review suggestions without writing")
    parser.add_argument("--apply-suggestions", action="store_true", help="Apply deterministic review suggestions; dry-run unless --confirm")
    parser.add_argument("--review-status", default="pending_review", help="Review status filter for --review-suggestions")
    parser.add_argument("--suggested-action", default="ready", help="Suggested action filter for --apply-suggestions")
    parser.add_argument("--suggestion-limit", type=int, default=100, help="Signal limit for --review-suggestions")
    parser.add_argument("--action", default="", help="Review action: ready/approve/reject/ignore/pending_review")
    parser.add_argument("--note", default="", help="Review note for --review-signal")
    parser.add_argument("--apply-review", action="store_true", help="Write --review-signal decision; default is dry-run")
    parser.add_argument("--json", action="store_true", help="Print full JSON payload to stdout")
    return parser.parse_args()


def _reject_forbidden_flags(argv: list[str]) -> None:
    used = sorted(FORBIDDEN_WRITE_FLAGS.intersection(argv))
    if used:
        raise ValueError(f"P8-1 preview rejects write/provider/crawler flags: {', '.join(used)}")


def main() -> int:
    try:
        _reject_forbidden_flags(sys.argv[1:])
        args = parse_args()
        if args.review_suggestions:
            payload = build_competitor_signal_review_suggestions(
                review_status=args.review_status,
                limit=args.suggestion_limit,
            )
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
            else:
                print(format_review_suggestions(payload))
            return 0
        if args.apply_suggestions:
            payload = apply_competitor_signal_review_suggestions(
                review_status=args.review_status,
                suggested_action=args.suggested_action,
                limit=args.suggestion_limit,
                actor="cli",
                dry_run=not args.confirm,
            )
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
            else:
                print(format_apply_suggestions(payload))
                if payload.get("dry_run"):
                    print("Add --confirm to write these decisions.")
            return 0
        if args.review_signal:
            result = review_competitor_signal(
                args.review_signal,
                action=args.action,
                note=args.note,
                actor="cli",
                dry_run=not args.apply_review,
            )
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            else:
                print(f"signal_id={int(result.get('id') or 0)}")
                print(f"brand={result.get('brand') or ''}")
                print(f"previous_status={result.get('previous_status') or ''}")
                print(f"review_status={result.get('review_status') or ''}")
                print(f"dry_run={str(bool(result.get('dry_run'))).lower()}")
                print(f"write_db={str(bool(result.get('write_db'))).lower()}")
                if result.get("dry_run"):
                    print("Add --apply-review to write this decision.")
            return 0
        if args.commit_signals:
            if not args.confirm:
                raise ValueError("--commit-signals requires --confirm")
            result = commit_competitor_signals(limit=args.limit, committed_by=args.committed_by)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            else:
                print(f"scenario={result.get('scenario', '')}")
                print(f"run_uid={result.get('run_uid', '')}")
                print(f"run_id={int(result.get('run_id') or 0)}")
                print(f"inserted_signals={int(result.get('inserted_signals') or 0)}")
                print(f"provider_calls={str(bool(result.get('provider_calls'))).lower()}")
                print(f"write_db={str(bool(result.get('write_db'))).lower()}")
            return 0
        payload = build_competitor_brain_preview(
            limit=args.limit,
            json_out=args.json_out,
            md_out=args.md_out,
        )
        if args.json:
            print(json.dumps({key: value for key, value in payload.items() if key != "markdown"}, ensure_ascii=False, indent=2, default=str))
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
