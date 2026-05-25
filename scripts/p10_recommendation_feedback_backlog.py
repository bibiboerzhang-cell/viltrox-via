#!/usr/bin/env python3
"""P10 read-only recommendation feedback backlog CLI."""
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
from app.domains.recommendations.feedback_backlog import (  # noqa: E402
    build_recommendation_feedback_backlog,
    format_recommendation_feedback_backlog,
)
from app.domains.recommendations import product_analysis  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a read-only P10 recommendation feedback backlog.")
    parser.add_argument("--run-uid", default="", help="Filter to one recommendation run UID")
    parser.add_argument("--limit", type=int, default=100, help="Backlog row limit, default 100, max 500")
    parser.add_argument("--json-out", default="", help="Write JSON output to this path")
    parser.add_argument("--md-out", default="", help="Write Markdown output to this path")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    parser.add_argument("--recommendation-id", type=int, default=0, help="Explicit recommendation id to write feedback for")
    parser.add_argument("--action", choices=["shortlist", "reject", "feedback"], default="feedback", help="Action for --recommendation-id")
    parser.add_argument("--note", default="", help="Feedback note")
    parser.add_argument("--reason", default="", help="Reject reason")
    parser.add_argument("--confirm", action="store_true", help="Required to write --recommendation-id action")
    return parser.parse_args()


def _action_payload(args: argparse.Namespace) -> dict:
    payload = {
        "source": "p10_recommendation_feedback_backlog_cli",
    }
    if args.note:
        payload["note"] = args.note
    if args.reason:
        payload["reason"] = args.reason
    if args.action == "feedback" and not args.note:
        payload["note"] = "P10 CLI marked this recommendation for human review."
    if args.action == "reject" and not args.reason:
        payload["reason"] = "P10 CLI manual rejection."
    return payload


def main() -> int:
    try:
        args = parse_args()
        if args.recommendation_id:
            payload = _action_payload(args)
            if not args.confirm:
                result = {
                    "scenario": "p10_recommendation_feedback_action",
                    "dry_run": True,
                    "write_db": False,
                    "provider_calls": False,
                    "recommendation_id": args.recommendation_id,
                    "action": args.action,
                    "payload": payload,
                    "message": "Add --confirm to write this recommendation action.",
                }
            else:
                result = {
                    "scenario": "p10_recommendation_feedback_action",
                    "dry_run": False,
                    "write_db": True,
                    "provider_calls": False,
                    "action": args.action,
                    **product_analysis.action_recommendation(
                        args.recommendation_id,
                        args.action,
                        payload,
                        staff={"id": None, "name": "p10-cli"},
                    ),
                }
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            else:
                print(f"scenario={result.get('scenario', '')}")
                print(f"dry_run={str(bool(result.get('dry_run'))).lower()}")
                print(f"write_db={str(bool(result.get('write_db'))).lower()}")
                print(f"provider_calls={str(bool(result.get('provider_calls'))).lower()}")
                print(f"recommendation_id={args.recommendation_id}")
                print(f"action={args.action}")
                if result.get("feedback_inserted") is not None:
                    print(f"feedback_inserted={str(bool(result.get('feedback_inserted'))).lower()}")
                if result.get("message"):
                    print(result["message"])
            return 0
        payload = build_recommendation_feedback_backlog(
            run_uid=args.run_uid,
            limit=args.limit,
            json_out=args.json_out,
            md_out=args.md_out,
        )
        if args.json:
            print(json.dumps({key: value for key, value in payload.items() if key != "markdown"}, ensure_ascii=False, indent=2, default=str))
        else:
            print(format_recommendation_feedback_backlog(payload))
            if args.json_out:
                print(f"json_out={args.json_out}")
            if args.md_out:
                print(f"md_out={args.md_out}")
        return 0
    finally:
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    raise SystemExit(main())
