#!/usr/bin/env python3
"""Read-only P4.54 evidence summary acceptance report."""
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
import app.domains.evidence.summary as evidence_summary  # noqa: E402
from app.services.vkpi import natural_search  # noqa: E402


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


def build_report(*, query: str = "viltrox", kol_pool_id: int = 0, include_product_fit: bool = True) -> dict[str, Any]:
    candidate = {"kol_pool_id": int(kol_pool_id or 0)}
    if not candidate["kol_pool_id"]:
        candidate = _candidate_from_search(query, limit=20)
    payload = evidence_summary.build_kol_pool_evidence_summary(
        int(candidate["kol_pool_id"]),
        include_product_fit=include_product_fit,
        include_llm_preflight=True,
    )
    summaries = payload.get("summaries") if isinstance(payload.get("summaries"), list) else []
    llm_preflight = payload.get("llm_budget_preflight") if isinstance(payload.get("llm_budget_preflight"), dict) else {}
    checks = {
        "candidate_found": _int(candidate.get("kol_pool_id")) > 0,
        "summary_passed": bool(payload.get("passed")),
        "summaries_exist": len(summaries) >= 4,
        "all_summaries_traceable": all(bool((item or {}).get("evidence_refs")) for item in summaries),
        "no_provider_calls": not bool(payload.get("provider_calls")),
        "no_llm_calls": not bool(payload.get("llm_calls")),
        "no_write_db": not bool(payload.get("write_db")),
        "new_fact_generation_disabled": bool((payload.get("policy") or {}).get("new_fact_generation") is False),
        "llm_preflight_readonly": not bool(llm_preflight.get("provider_calls_made")),
    }
    return {
        "mode": "read_only_p4_54_evidence_summary_acceptance",
        "generated_at": _now(),
        "query": query,
        "candidate": candidate,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "summary": {
            "summary_count": _int(payload.get("summary_count")),
            "evidence_ref_count": _int(payload.get("evidence_ref_count")),
            "sections": [item.get("section") for item in summaries if isinstance(item, dict)],
            "llm_gate_reason": llm_preflight.get("provider_gate_reason") or "",
        },
        "evidence_summary": payload,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# V-KPI P4.54 Evidence Summary Acceptance",
        "",
        "Read-only acceptance report. It summarizes existing evidence only; no provider, LLM, task, sync, or DB write is performed.",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Passed: `{str(report['passed']).lower()}`",
        f"- Query: `{report['query']}`",
        f"- KOL pool ID: `{report['candidate'].get('kol_pool_id') or 'none'}`",
        f"- Summary count: `{summary['summary_count']}`",
        f"- Evidence refs: `{summary['evidence_ref_count']}`",
        f"- LLM gate reason: `{summary['llm_gate_reason'] or 'not_checked'}`",
        "",
        "## Checks",
        "",
    ]
    for key, value in report["checks"].items():
        lines.append(f"- `{key}`: `{str(bool(value)).lower()}`")
    lines.extend(["", "## Sections", "", f"`{summary['sections']}`"])
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a read-only P4.54 evidence summary acceptance report.")
    parser.add_argument("--query", default="viltrox")
    parser.add_argument("--kol-pool-id", type=int, default=0)
    parser.add_argument("--skip-product-fit", action="store_true")
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
            include_product_fit=not bool(args.skip_product_fit),
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
