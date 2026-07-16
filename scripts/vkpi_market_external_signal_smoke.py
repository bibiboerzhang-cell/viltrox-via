#!/usr/bin/env python3
"""Build a read-only Google News RSS / RSS external market signal smoke report."""
from __future__ import annotations

from stdout_utils import out as stdout_out

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import close_db_runtime  # noqa: E402
from app.domains.market.external_signal_reports import (  # noqa: E402
    render_external_daily_candidate_plan_markdown,
    render_external_signal_smoke_markdown,
    write_external_daily_candidate_plan,
    write_external_signal_smoke,
)
from app.domains.market.external_signal_smoke import (  # noqa: E402
    build_external_daily_candidate_plan,
    build_external_source_matrix,
    build_external_signal_smoke,
)


def _source_from_args(args: argparse.Namespace) -> list[dict[str, str]] | None:
    if args.query:
        return [
            {
                "source_key": "google_news_custom",
                "provider": "google_news",
                "source_type": "google_news_rss",
                "query": args.query,
                "purpose": "manual_google_news_smoke",
            }
        ]
    if args.feed_url:
        return [
            {
                "source_key": "rss_custom",
                "provider": "rss",
                "source_type": "rss_feed",
                "feed_url": args.feed_url,
                "purpose": "manual_rss_smoke",
            }
        ]
    return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-http-fetch", action="store_true", help="Fetch one tiny allowlisted live sample")
    parser.add_argument("--source-key", default="", help="Limit default source matrix to one source_key")
    parser.add_argument("--source-group", default="", help="Limit default source matrix to one source_group")
    parser.add_argument("--source-matrix", action="store_true", help="Print the source matrix instead of running smoke")
    parser.add_argument("--daily-plan", action="store_true", help="Print a read-only daily candidate plan without HTTP")
    parser.add_argument("--query", default="", help="Use one custom Google News RSS query")
    parser.add_argument("--feed-url", default="", help="Use one custom allowlisted RSS URL")
    parser.add_argument("--limit-per-source", type=int, default=5)
    parser.add_argument("--max-http-calls", type=int, default=6, help="Bound planned HTTP calls for --daily-plan")
    parser.add_argument("--timeout-seconds", type=int, default=8)
    parser.add_argument("--json-out", default="")
    parser.add_argument("--md-out", default="")
    parser.add_argument("--out-dir", default="", help="Write timestamped JSON/Markdown into this directory")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.source_matrix:
            report = build_external_source_matrix(source_group=args.source_group)
            stdout_out(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            return 0 if report.get("passed") else 2
        if args.daily_plan:
            report = build_external_daily_candidate_plan(
                source_group=args.source_group,
                max_http_calls=args.max_http_calls,
                limit_per_source=args.limit_per_source,
            )
            paths: dict[str, str] = {}
            if args.out_dir:
                paths = write_external_daily_candidate_plan(report, out_dir=args.out_dir)
            if args.json_out:
                out = Path(args.json_out)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                paths["json_path"] = str(out.resolve())
            if args.md_out:
                out = Path(args.md_out)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(render_external_daily_candidate_plan_markdown(report), encoding="utf-8")
                paths["md_path"] = str(out.resolve())
            stdout_out(
                json.dumps(
                    {
                        **paths,
                        "passed": report["passed"],
                        "summary": report["summary"],
                        "checks": report["checks"],
                        "provider_calls": report["provider_calls"],
                        "external_http_calls": report["external_http_calls"],
                        "write_db": report["write_db"],
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
            return 0 if report.get("passed") else 2
        report = build_external_signal_smoke(
            sources=_source_from_args(args),
            execute_http_fetch=args.execute_http_fetch,
            source_key=args.source_key,
            source_group=args.source_group,
            limit_per_source=args.limit_per_source,
            timeout_seconds=args.timeout_seconds,
        )
        paths: dict[str, str] = {}
        if args.out_dir:
            paths = write_external_signal_smoke(report, out_dir=args.out_dir)
        if args.json_out:
            out = Path(args.json_out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            paths["json_path"] = str(out.resolve())
        if args.md_out:
            out = Path(args.md_out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(render_external_signal_smoke_markdown(report), encoding="utf-8")
            paths["md_path"] = str(out.resolve())
        stdout_out(
            json.dumps(
                {
                    **paths,
                    "passed": report["passed"],
                    "summary": report["summary"],
                    "checks": report["checks"],
                    "provider_calls": report["provider_calls"],
                    "external_http_calls": report["external_http_calls"],
                    "write_db": report["write_db"],
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return 0 if report.get("passed") else 2
    finally:
        import asyncio

        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    raise SystemExit(main())
