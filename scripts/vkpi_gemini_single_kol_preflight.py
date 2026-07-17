#!/usr/bin/env python3
"""Read-only P4.55 Gemini single-KOL preflight report."""
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
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# Path patch must precede this import: under pytest the module loads as
# scripts.<name>, so scripts/ is not sys.path[0] and stdout_utils would
# raise ModuleNotFoundError at collection time.
from stdout_utils import out as stdout_out  # noqa: E402

from app.db.connection import close_db_runtime  # noqa: E402
from app.domains.intelligence import gemini_single_kol_preflight  # noqa: E402
from app.domains.search import natural_search  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _int(value: Any) -> int:
    try:
        return int(float(str(value or "").replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


def _candidate_from_search(query: str, *, limit: int) -> dict[str, Any]:
    payload = natural_search.search(query, limit=limit)
    for item in payload.get("items") or []:
        if isinstance(item, dict) and item.get("source_table") == "vkpi_kol_pool" and _int(item.get("source_id")):
            return {
                "kol_pool_id": _int(item.get("source_id")),
                "title": item.get("title") or "",
                "platform": item.get("platform") or "",
                "handle": item.get("handle") or "",
                "search_total": _int(payload.get("total")),
            }
    return {"kol_pool_id": 0, "search_total": _int(payload.get("total"))}


def _checks_from_payload(candidate: dict[str, Any], payload: dict[str, Any], run: dict[str, Any] | None = None) -> dict[str, bool]:
    if run and bool(run.get("executed")):
        run_checks = run.get("checks") if isinstance(run.get("checks"), dict) else {}
        return {
            "candidate_found": _int(candidate.get("kol_pool_id")) > 0,
            "executed": True,
            "provider_calls_explicit": bool(run_checks.get("provider_call_was_explicit")),
            "budget_gate_passed": bool(run_checks.get("budget_gate_passed")),
            "ledger_recorded": bool(run_checks.get("ledger_recorded")),
            "no_business_write_db": not bool(run.get("business_write_db")),
            "no_sync_triggered": not bool(run.get("sync_triggered")),
            "no_task_enqueued": not bool(run.get("task_enqueued")),
        }
    return {
        "candidate_found": _int(candidate.get("kol_pool_id")) > 0,
        "preflight_completed": bool(payload.get("checks", {}).get("preflight_completed")),
        "candidate_evaluated": bool(payload.get("checks", {}).get("candidate_evaluated")),
        "url_readiness_checked": bool(payload.get("checks", {}).get("url_readiness_checked")),
        "budget_preflight_readonly": bool(payload.get("checks", {}).get("budget_preflight_readonly")),
        "no_provider_calls": not bool((run or payload).get("provider_calls")),
        "no_llm_calls": not bool((run or payload).get("llm_calls")),
        "no_write_db": not bool((run or payload).get("write_db")),
        "no_business_write_db": not bool((run or payload).get("business_write_db")),
        "no_sync_triggered": not bool((run or payload).get("sync_triggered")),
        "no_task_enqueued": not bool((run or payload).get("task_enqueued")),
    }


def build_report(*, query: str = "viltrox", kol_pool_id: int = 0, candidate_limit: int = 24) -> dict[str, Any]:
    candidate = {"kol_pool_id": int(kol_pool_id or 0)}
    if not candidate["kol_pool_id"]:
        candidate = _candidate_from_search(query, limit=20)
    payload: dict[str, Any] = {}
    if _int(candidate.get("kol_pool_id")):
        payload = gemini_single_kol_preflight.build_kol_pool_gemini_preflight(
            int(candidate["kol_pool_id"]),
            candidate_limit=candidate_limit,
            include_budget_preflight=True,
        )
    checks = _checks_from_payload(candidate, payload)
    go_no_go = payload.get("go_no_go") if isinstance(payload.get("go_no_go"), dict) else {}
    budget = payload.get("budget_preflight") if isinstance(payload.get("budget_preflight"), dict) else {}
    readiness = payload.get("url_readiness") if isinstance(payload.get("url_readiness"), dict) else {}
    return {
        "mode": "read_only_p4_55_gemini_single_kol_preflight_acceptance",
        "generated_at": _now(),
        "query": query,
        "candidate": candidate,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "business_write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "summary": {
            "candidate_count": _int((payload.get("candidate_strategy") or {}).get("candidate_count") if payload else 0),
            "top_video_url": (payload.get("top_candidate") or {}).get("video_url") or (payload.get("top_candidate") or {}).get("url") if payload else "",
            "valid_video_url": bool(readiness.get("valid_video_url")),
            "provider_path": readiness.get("provider_path") or "",
            "blocked_reason": go_no_go.get("blocked_reason") or readiness.get("blocked_reason") or "",
            "provider_gate_reason": budget.get("provider_gate_reason") or "",
            "ready_for_manual_live_test": bool(go_no_go.get("ready_for_manual_live_test")),
        },
        "preflight": payload,
    }


async def build_run_report(
    *,
    query: str = "viltrox",
    kol_pool_id: int = 0,
    candidate_limit: int = 24,
    execute: bool = False,
    allow_provider_calls: bool = False,
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    candidate = {"kol_pool_id": int(kol_pool_id or 0)}
    if not candidate["kol_pool_id"]:
        candidate = _candidate_from_search(query, limit=20)
    run: dict[str, Any] = {}
    payload: dict[str, Any] = {}
    if _int(candidate.get("kol_pool_id")):
        run = await gemini_single_kol_preflight.run_kol_pool_gemini_single(
            int(candidate["kol_pool_id"]),
            candidate_limit=candidate_limit,
            execute=execute,
            allow_provider_calls=allow_provider_calls,
            timeout_seconds=timeout_seconds,
        )
        payload = run.get("preflight") if isinstance(run.get("preflight"), dict) else {}
    checks = _checks_from_payload(candidate, payload, run=run)
    go_no_go = payload.get("go_no_go") if isinstance(payload.get("go_no_go"), dict) else {}
    budget = payload.get("budget_preflight") if isinstance(payload.get("budget_preflight"), dict) else {}
    readiness = payload.get("url_readiness") if isinstance(payload.get("url_readiness"), dict) else {}
    return {
        "mode": "controlled_p4_55_gemini_single_kol_run_acceptance",
        "generated_at": _now(),
        "query": query,
        "candidate": candidate,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "provider_calls": bool(run.get("provider_calls")),
        "llm_calls": bool(run.get("llm_calls")),
        "write_db": bool(run.get("write_db")),
        "business_write_db": bool(run.get("business_write_db")),
        "sync_triggered": bool(run.get("sync_triggered")),
        "task_enqueued": bool(run.get("task_enqueued")),
        "summary": {
            "execution_status": run.get("execution_status") or "not_started",
            "run_reason": run.get("reason") or "",
            "candidate_count": _int((payload.get("candidate_strategy") or {}).get("candidate_count") if payload else 0),
            "top_video_url": (payload.get("top_candidate") or {}).get("video_url") or (payload.get("top_candidate") or {}).get("url") if payload else "",
            "valid_video_url": bool(readiness.get("valid_video_url")),
            "provider_path": readiness.get("provider_path") or "",
            "blocked_reason": run.get("reason") or go_no_go.get("blocked_reason") or readiness.get("blocked_reason") or "",
            "provider_gate_reason": budget.get("provider_gate_reason") or "",
            "ready_for_manual_live_test": bool(go_no_go.get("ready_for_manual_live_test")),
            "executed": bool(run.get("executed")),
        },
        "run": run,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    executed = bool(summary.get("executed"))
    lines = [
        "# V-KPI P4.55 Gemini Single-KOL Preflight",
        "",
        (
            "Controlled live-run report. Provider calls were explicitly requested and budget-gated; business DB writes, sync, and task enqueue stay disabled."
            if executed
            else "Read-only readiness report. It selects a cached Top1 video candidate and checks budget gates; no Gemini, LLM, Apify, sync, task, or DB write is performed."
        ),
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Passed: `{str(report['passed']).lower()}`",
        f"- Query: `{report['query']}`",
        f"- KOL pool ID: `{report['candidate'].get('kol_pool_id') or 'none'}`",
        f"- Candidate count: `{summary['candidate_count']}`",
        f"- Valid video URL: `{str(summary['valid_video_url']).lower()}`",
        f"- Provider path: `{summary['provider_path'] or 'none'}`",
        f"- Provider gate reason: `{summary['provider_gate_reason'] or 'not_checked'}`",
        f"- Ready for manual live test: `{str(summary['ready_for_manual_live_test']).lower()}`",
        f"- Execution status: `{summary.get('execution_status', 'preflight_only')}`",
        f"- Blocked reason: `{summary['blocked_reason'] or 'none'}`",
        "",
        "## Checks",
        "",
    ]
    for key, value in report["checks"].items():
        lines.append(f"- `{key}`: `{str(bool(value)).lower()}`")
    top_url = summary.get("top_video_url") or ""
    lines.extend(["", "## Top Candidate", "", f"`{top_url or 'none'}`"])
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a read-only P4.55 Gemini single-KOL preflight report.")
    parser.add_argument("--query", default="viltrox")
    parser.add_argument("--kol-pool-id", type=int, default=0)
    parser.add_argument("--candidate-limit", type=int, default=24)
    parser.add_argument("--execute", action="store_true", help="Request the live-run path. Requires --allow-provider-calls to call Gemini.")
    parser.add_argument("--allow-provider-calls", action="store_true", help="Allow the live-run path to call Gemini if budget gates pass.")
    parser.add_argument("--timeout-seconds", type=int, default=900)
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
        if args.execute or args.allow_provider_calls:
            report = await build_run_report(
                query=str(args.query or "viltrox"),
                kol_pool_id=max(0, int(args.kol_pool_id or 0)),
                candidate_limit=max(1, min(100, int(args.candidate_limit or 24))),
                execute=bool(args.execute),
                allow_provider_calls=bool(args.allow_provider_calls),
                timeout_seconds=max(30, min(3600, int(args.timeout_seconds or 900))),
            )
        else:
            report = build_report(
                query=str(args.query or "viltrox"),
                kol_pool_id=max(0, int(args.kol_pool_id or 0)),
                candidate_limit=max(1, min(100, int(args.candidate_limit or 24))),
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
