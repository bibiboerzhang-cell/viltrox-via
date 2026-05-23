#!/usr/bin/env python3
"""Plan or explicitly execute qualified KOL Apify batch refreshes.

Default mode is safe: it builds the qualified batch plan and runs the executor
with provider calls blocked. Real Apify calls require both ``--execute`` and
``--allow-provider-calls``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
DEFAULT_OPS_DIR = ROOT / "runtime" / "ops"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import close_db_runtime  # noqa: E402
from app.services.vkpi import apify_batch_refresh  # noqa: E402


def _platforms(value: str) -> set[str]:
    return {apify_batch_refresh.normalize_platform(item) for item in str(value or "").split(",") if item.strip()}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan or explicitly execute V-KPI qualified Apify batch refresh")
    parser.add_argument("--limit", type=int, default=50, help="Max qualified KOL rows to plan")
    parser.add_argument("--offset", type=int, default=0, help="Qualified selector offset")
    parser.add_argument("--platforms", default="", help="Comma-separated platform filter")
    parser.add_argument("--tiers", default="hot", help="Comma-separated refresh tiers")
    parser.add_argument("--stale-before", default="", help="Only plan rows refreshed before this UTC timestamp")
    parser.add_argument("--stale-days", type=int, default=0, help="Compute stale-before as now minus N days")
    parser.add_argument("--max-posts", type=int, default=1, help="Latest post sample per KOL")
    parser.add_argument("--max-concurrent-runs", type=int, default=2, help="Outer Apify run concurrency cap")
    parser.add_argument("--chunk-sizes", default="", help="Optional platform chunk overrides, e.g. instagram=25,tiktok=10")
    parser.add_argument("--timeout-seconds", type=int, default=apify_batch_refresh.DEFAULT_RUN_TIMEOUT_SECONDS, help="Apify actor timeout if real execution is explicitly enabled")
    parser.add_argument("--execute", action="store_true", help="Run the executor. Provider calls still require --allow-provider-calls.")
    parser.add_argument("--allow-provider-calls", action="store_true", help="Actually call Apify. Use only after backup-first operator approval.")
    parser.add_argument("--compact", action="store_true", help="Omit full batch target lists from output")
    parser.add_argument("--json-out", default="", help="Optional JSON artifact path. Defaults to runtime/ops.")
    parser.add_argument("--no-artifact", action="store_true", help="Do not write an operator JSON artifact")
    return parser.parse_args(argv)


def _compact_plan(plan: dict[str, Any]) -> dict[str, Any]:
    result = dict(plan)
    result["batches"] = [
        {
            "batch_key": batch.get("batch_key"),
            "platform": batch.get("platform"),
            "target_count": batch.get("target_count"),
            "actor_id": batch.get("actor_id"),
            "kol_pool_ids": batch.get("kol_pool_ids"),
        }
        for batch in (plan.get("batches") or [])
        if isinstance(batch, dict)
    ]
    return result


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    return apify_batch_refresh.qualified_apify_batch_plan(
        limit=max(1, min(1200, int(args.limit or 50))),
        offset=max(0, min(5000, int(args.offset or 0))),
        stale_before=str(args.stale_before or ""),
        stale_days=max(0, int(args.stale_days or 0)),
        platforms=_platforms(args.platforms),
        tiers=_platforms(args.tiers),
        max_posts=max(1, min(3, int(args.max_posts or 1))),
        max_concurrent=max(1, min(3, int(args.max_concurrent_runs or 2))),
        chunk_overrides=apify_batch_refresh.parse_chunk_overrides(args.chunk_sizes),
    )


async def run_from_args(args: argparse.Namespace) -> dict[str, Any]:
    plan = build_plan(args)
    execution = await apify_batch_refresh.execute_apify_batch_plan(
        plan,
        allow_provider_calls=bool(args.execute and args.allow_provider_calls),
        timeout_secs=max(30, min(1800, int(args.timeout_seconds or apify_batch_refresh.DEFAULT_RUN_TIMEOUT_SECONDS))),
    )
    return {
        "mode": "execute" if args.execute else "plan_with_blocked_executor",
        "provider_calls_allowed": bool(args.execute and args.allow_provider_calls),
        "plan": _compact_plan(plan) if args.compact else plan,
        "execution": execution,
    }


def _artifact_path(args: argparse.Namespace) -> Path:
    if args.json_out:
        path = Path(str(args.json_out)).expanduser()
        return path if path.is_absolute() else ROOT / path
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    mode = "execute" if args.execute and args.allow_provider_calls else "plan"
    return DEFAULT_OPS_DIR / f"{now}-apify-batch-refresh-{mode}.json"


def write_artifact(result: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    if args.no_artifact:
        return result
    path = _artifact_path(args)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(result)
    payload["artifact"] = {
        "path": str(path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider_calls_allowed": bool(args.execute and args.allow_provider_calls),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, default=str, indent=2) + "\n", encoding="utf-8")
    return payload


async def async_main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = write_artifact(await run_from_args(args), args)
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
        execution = result.get("execution") if isinstance(result, dict) else {}
        if args.execute and args.allow_provider_calls and isinstance(execution, dict):
            if int(execution.get("failed_batches") or 0):
                return 2
        return 0
    finally:
        await close_db_runtime()


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
