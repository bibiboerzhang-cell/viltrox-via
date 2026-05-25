#!/usr/bin/env python3
"""Build the P7.82 read-only Recommendation Agent report."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.connection import close_db_runtime  # noqa: E402
from app.domains.intelligence import recommendation_use_case as recommendation_agent_v0  # noqa: E402


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def render_markdown(report: dict[str, Any]) -> str:
    summary = _as_dict(report.get("summary"))
    lines = [
        "# V-KPI P7.82 Recommendation Agent v0",
        "",
        "Read-only recommendation candidate planner. It proposes candidates for human review only.",
        "",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Passed: `{str(bool(report.get('passed'))).lower()}`",
        f"- Agent status: `{summary.get('agent_status')}`",
        f"- Evidence source: `{summary.get('evidence_source')}`",
        f"- Chains: `{summary.get('chain_count')}`",
        f"- Candidates: `{summary.get('candidate_count')}`",
        f"- Blocked: `{summary.get('blocked_count')}`",
        f"- Feedback contexts: `{summary.get('feedback_context_count')}`",
        "",
        "## Candidates",
        "",
    ]
    for item in report.get("candidates") or []:
        identity = _as_dict(item.get("item"))
        lines.append(
            f"- #{item.get('rank')} kol_pool_id=`{item.get('kol_pool_id')}` "
            f"@{identity.get('handle') or '-'} decision=`{item.get('suggested_decision')}` "
            f"score=`{item.get('score')}` confidence=`{item.get('confidence')}` "
            f"refs=`{item.get('evidence_ref_count')}`"
        )
    if not report.get("candidates"):
        lines.append("- none")
    lines.extend(["", "## Blocked", ""])
    for item in report.get("blocked_candidates") or []:
        lines.append(f"- kol_pool_id=`{item.get('kol_pool_id')}` reason=`{item.get('reason')}`")
    if not report.get("blocked_candidates"):
        lines.append("- none")
    lines.extend(["", "## Checks", ""])
    for key, value in _as_dict(report.get("checks")).items():
        lines.append(f"- `{key}`: `{str(bool(value)).lower()}`")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Recommendation Agent v0 report.")
    parser.add_argument("--kol-pool-ids", default="", help="Comma-separated kol_pool IDs. If omitted, reads latest P7.81 artifact or P6.77 through Evidence Agent.")
    parser.add_argument("--ops-dir", default="runtime/ops")
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--min-evidence-refs", type=int, default=3)
    parser.add_argument("--ref-limit", type=int, default=12)
    parser.add_argument("--claim-limit", type=int, default=12)
    parser.add_argument("--fresh-evidence", action="store_true", help="Ignore latest P7.81 artifact and rebuild evidence chains read-only.")
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
        report = recommendation_agent_v0.build_recommendation_agent_v0(
            kol_pool_ids=args.kol_pool_ids,
            ops_dir=args.ops_dir,
            limit=args.limit,
            min_evidence_refs=args.min_evidence_refs,
            ref_limit=args.ref_limit,
            claim_limit=args.claim_limit,
            use_latest_evidence_artifact=not bool(args.fresh_evidence),
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
