#!/usr/bin/env python3
"""Read-only P4.56 Gemini go/no-go report."""
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


def build_report(*, query: str = "viltrox", kol_pool_id: int = 0, candidate_limit: int = 24) -> dict[str, Any]:
    candidate = {"kol_pool_id": int(kol_pool_id or 0)}
    if not candidate["kol_pool_id"]:
        candidate = _candidate_from_search(query, limit=20)
    payload: dict[str, Any] = {}
    if _int(candidate.get("kol_pool_id")):
        payload = gemini_single_kol_preflight.build_kol_pool_gemini_go_no_go(
            int(candidate["kol_pool_id"]),
            candidate_limit=candidate_limit,
        )
    checks = payload.get("checks") if isinstance(payload.get("checks"), dict) else {}
    checks = {
        "candidate_found": _int(candidate.get("kol_pool_id")) > 0,
        **checks,
        "no_provider_calls": not bool(payload.get("provider_calls")),
        "no_llm_calls": not bool(payload.get("llm_calls")),
        "no_write_db": not bool(payload.get("write_db")),
        "no_sync_triggered": not bool(payload.get("sync_triggered")),
        "no_task_enqueued": not bool(payload.get("task_enqueued")),
    }
    return {
        "mode": "read_only_p4_56_gemini_go_no_go_acceptance",
        "generated_at": _now(),
        "query": query,
        "candidate": candidate,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "summary": {
            "decision": payload.get("decision") or "not_evaluated",
            "decision_reason": payload.get("decision_reason") or "",
            "blockers": payload.get("blockers") if isinstance(payload.get("blockers"), list) else [],
            "candidate_count": _int((payload.get("summary") or {}).get("candidate_count")),
            "valid_video_url": bool((payload.get("summary") or {}).get("valid_video_url")),
            "provider_path": (payload.get("summary") or {}).get("provider_path") or "",
            "top_video_url": (payload.get("summary") or {}).get("top_video_url") or "",
            "provider_gate_reason": (payload.get("summary") or {}).get("provider_gate_reason") or "",
            "ready_for_manual_live_test": bool((payload.get("summary") or {}).get("ready_for_manual_live_test")),
        },
        "go_no_go_report": payload,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# V-KPI P4.56 Gemini Go/No-Go Report",
        "",
        "Read-only decision report. It uses cached preflight evidence and budget gates only; no Gemini, LLM, Apify, sync, task, or DB write is performed.",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Passed: `{str(report['passed']).lower()}`",
        f"- Query: `{report['query']}`",
        f"- KOL pool ID: `{report['candidate'].get('kol_pool_id') or 'none'}`",
        f"- Decision: `{summary['decision']}`",
        f"- Decision reason: `{summary['decision_reason'] or 'none'}`",
        f"- Candidate count: `{summary['candidate_count']}`",
        f"- Valid video URL: `{str(summary['valid_video_url']).lower()}`",
        f"- Provider path: `{summary['provider_path'] or 'none'}`",
        f"- Provider gate reason: `{summary['provider_gate_reason'] or 'not_checked'}`",
        f"- Ready for manual live test: `{str(summary['ready_for_manual_live_test']).lower()}`",
        "",
        "## Blockers",
        "",
    ]
    blockers = summary.get("blockers") or []
    if blockers:
        lines.extend(f"- `{item}`" for item in blockers)
    else:
        lines.append("- `none`")
    lines.extend(["", "## Checks", ""])
    for key, value in report["checks"].items():
        lines.append(f"- `{key}`: `{str(bool(value)).lower()}`")
    lines.extend(["", "## Top Candidate", "", f"`{summary.get('top_video_url') or 'none'}`"])
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a read-only P4.56 Gemini go/no-go report.")
    parser.add_argument("--query", default="viltrox")
    parser.add_argument("--kol-pool-id", type=int, default=0)
    parser.add_argument("--candidate-limit", type=int, default=24)
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
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        else:
            print(markdown)
        return 0 if report.get("passed") else 3
    finally:
        await close_db_runtime()


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
