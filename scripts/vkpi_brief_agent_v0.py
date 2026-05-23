#!/usr/bin/env python3
"""Build the P7.83 read-only Brief Agent report."""
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
from app.services.vkpi import brief_agent_v0  # noqa: E402


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def render_markdown(report: dict[str, Any]) -> str:
    summary = _as_dict(report.get("summary"))
    lines = [
        "# V-KPI P7.83 Brief Agent v0",
        "",
        "Read-only operator brief. It summarizes existing recommendation candidates with evidence refs only.",
        "",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Passed: `{str(bool(report.get('passed'))).lower()}`",
        f"- Agent status: `{summary.get('agent_status')}`",
        f"- Source: `{summary.get('recommendation_source')}`",
        f"- Candidates: `{summary.get('candidate_count')}`",
        f"- Selected: `{summary.get('selected_candidate_count')}`",
        f"- Brief items: `{summary.get('brief_item_count')}`",
        f"- Next actions: `{summary.get('next_action_count')}`",
        f"- Evidence backlinks: `{summary.get('evidence_backlink_count')}`",
        "",
        "## Brief Items",
        "",
    ]
    for item in report.get("brief_items") or []:
        identity = _as_dict(item.get("identity"))
        lines.append(
            f"- `{item.get('brief_type')}` @{identity.get('handle') or identity.get('kol_pool_id') or '-'} "
            f"priority=`{item.get('priority')}` refs=`{len(item.get('evidence_refs') or [])}` "
            f"title=`{item.get('title')}`"
        )
    if not report.get("brief_items"):
        lines.append("- none")
    lines.extend(["", "## Next Actions", ""])
    for action in report.get("next_actions") or []:
        identity = _as_dict(action.get("identity"))
        lines.append(
            f"- @{identity.get('handle') or identity.get('kol_pool_id') or '-'} "
            f"priority=`{action.get('priority')}` refs=`{len(action.get('evidence_refs') or [])}` "
            f"text=`{action.get('text')}`"
        )
    if not report.get("next_actions"):
        lines.append("- none")
    lines.extend(["", "## Checks", ""])
    for key, value in _as_dict(report.get("checks")).items():
        lines.append(f"- `{key}`: `{str(bool(value)).lower()}`")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Brief Agent v0 report.")
    parser.add_argument("--kol-pool-ids", default="", help="Comma-separated kol_pool IDs. If omitted, reads latest P7.82 artifact or rebuilds read-only.")
    parser.add_argument("--ops-dir", default="runtime/ops")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--min-evidence-refs", type=int, default=3)
    parser.add_argument("--ref-limit", type=int, default=8)
    parser.add_argument("--claim-limit", type=int, default=12)
    parser.add_argument("--fresh-recommendations", action="store_true", help="Ignore latest P7.82 artifact and rebuild recommendation candidates read-only.")
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
        report = brief_agent_v0.build_brief_agent_v0(
            kol_pool_ids=args.kol_pool_ids,
            ops_dir=args.ops_dir,
            limit=args.limit,
            min_evidence_refs=args.min_evidence_refs,
            ref_limit=args.ref_limit,
            claim_limit=args.claim_limit,
            use_latest_recommendation_artifact=not bool(args.fresh_recommendations),
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
