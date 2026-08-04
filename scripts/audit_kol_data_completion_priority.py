#!/usr/bin/env python3
"""Read-only CLI for KOL data-completion priority and source-bias diagnosis.

No provider, LLM, Apify, queue, or write path is imported or called.  The
database transaction is explicitly marked read-only and rolled back after the
aggregate report is printed.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
DEFAULT_LOCAL_DATABASE_URL = "postgresql://postgres@127.0.0.1:54329/viltrox2"


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--all", action="store_true", help="audit all KOL Pool rows")
    scope.add_argument("--session-id", type=int, help="audit de-duplicated candidates from one search session")
    parser.add_argument(
        "--anchor",
        action="append",
        default=[],
        help="independent required product anchor; repeat for multiple anchors",
    )
    parser.add_argument("--top", type=int, default=30, help="number of item priorities to include")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("VKPI_COMPLETION_AUDIT_DATABASE_URL", DEFAULT_LOCAL_DATABASE_URL),
    )
    return parser.parse_args()


def _assert_loopback(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"postgres", "postgresql"} or parsed.hostname not in LOOPBACK_HOSTS:
        raise SystemExit("completion audit refuses non-loopback PostgreSQL URLs")


def main() -> int:
    args = _args()
    _assert_loopback(args.database_url)
    if args.all and args.anchor and int(args.top) > 0:
        raise SystemExit(
            "product-anchor priority requires --session-id; use --all --top 0 only for coverage diagnostics"
        )
    for key in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "CLAUDE_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "APIFY_TOKEN",
        "APIFY_API_TOKEN",
    ):
        os.environ[key] = ""
    sys.path.insert(0, str(BACKEND))
    os.environ["DATABASE_URL"] = args.database_url
    os.environ["DB_RUNTIME_BACKEND"] = "postgres"
    os.environ["DB_USE_PGBOUNCER"] = "0"
    os.environ["ENABLE_SCHEDULER"] = "0"
    os.environ["VKPI_SKIP_DOTENV"] = "1"
    os.environ["PGOPTIONS"] = "-c default_transaction_read_only=on -c statement_timeout=120000"

    from app.db.connection import get_conn

    # Loading ``app.domains.kol`` executes its broad compatibility facade and
    # imports unrelated provider-ready modules.  This audit deliberately loads
    # only the leaf file so its process surface remains DB-read-only.
    module_path = BACKEND / "app" / "domains" / "kol" / "data_completion_priority.py"
    spec = importlib.util.spec_from_file_location("vkpi_kol_data_completion_priority_audit", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load data completion priority module")
    priority_module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = priority_module
    spec.loader.exec_module(priority_module)

    conn = get_conn()
    try:
        conn.execute("SET TRANSACTION READ ONLY")
        ids = None
        if args.session_id is not None:
            ids = priority_module.load_search_session_kol_ids(args.session_id, conn=conn)
        report = priority_module.generate_data_completion_priority(
            kol_pool_ids=ids,
            required_product_anchors=args.anchor,
            output_limit=max(0, min(500, int(args.top))),
            conn=conn,
        )
        report["scope"]["session_id"] = args.session_id
        report["scope"]["transaction"] = "read_only_rolled_back"
        sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    finally:
        conn.rollback()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
