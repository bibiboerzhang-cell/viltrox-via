#!/usr/bin/env python3
"""Read-only P4.61 LLM/Gemini phase acceptance and second go/no-go report."""
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
from scripts import (  # noqa: E402
    vkpi_ai_brief_acceptance,
    vkpi_gemini_batch30_dry_run,
    vkpi_gemini_go_no_go_report,
    vkpi_llm_budget_acceptance,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _int(value: Any) -> int:
    try:
        return int(float(str(value or "").replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _ids_from_text(value: str) -> list[int]:
    ids: list[int] = []
    for part in str(value or "").replace(";", ",").split(","):
        parsed = _int(part)
        if parsed > 0 and parsed not in ids:
            ids.append(parsed)
    return ids


def _provider_estimates(budget_report: dict[str, Any]) -> dict[str, Any]:
    preflight = _as_dict(budget_report.get("preflight"))
    rows = []
    for provider in _as_list(preflight.get("providers")):
        if not isinstance(provider, dict):
            continue
        rows.append(
            {
                "provider": provider.get("provider") or "",
                "configured": bool(provider.get("configured")),
                "estimated_cost_usd": _float(provider.get("estimated_cost_usd")),
                "budget_allowed": bool(provider.get("budget_allowed")),
                "provider_calls_allowed": bool(provider.get("provider_calls_allowed")),
                "scopes": provider.get("scopes") if isinstance(provider.get("scopes"), list) else [],
            }
        )
    return {
        "provider_gate_reason": preflight.get("provider_gate_reason") or "",
        "monthly_env_budget_usd": _float(preflight.get("monthly_env_budget_usd")),
        "monthly_env_remaining_usd": _float(preflight.get("monthly_env_remaining_usd")),
        "estimated_providers": rows,
        "min_estimated_cost_usd": min([row["estimated_cost_usd"] for row in rows], default=0.0),
        "provider_calls_allowed": bool(preflight.get("provider_calls_allowed")),
    }


def _single_live_decision(go_no_go_report: dict[str, Any]) -> dict[str, Any]:
    summary = _as_dict(go_no_go_report.get("summary"))
    decision = str(summary.get("decision") or "not_evaluated")
    if decision == "go_manual_single_call":
        status = "go_one_manual_paid_call_only"
        reason = "single_kol_candidate_and_budget_gate_ready"
    elif decision == "hold":
        status = "hold"
        reason = "provider_or_budget_gate_not_ready"
    elif decision == "no_go_for_this_kol":
        status = "no_go_for_this_kol"
        reason = "candidate_not_ready"
    else:
        status = "hold"
        reason = "not_evaluated"
    return {
        "status": status,
        "reason": reason,
        "source_decision": decision,
        "provider_gate_reason": summary.get("provider_gate_reason") or "",
        "valid_video_url": bool(summary.get("valid_video_url")),
        "ready_for_manual_live_test": bool(summary.get("ready_for_manual_live_test")),
        "top_video_url": summary.get("top_video_url") or "",
        "blockers": summary.get("blockers") if isinstance(summary.get("blockers"), list) else [],
    }


def _batch_decision(batch_report: dict[str, Any], single_decision: dict[str, Any]) -> dict[str, Any]:
    readiness = str(batch_report.get("readiness") or "")
    status = "hold"
    reason = "batch_executor_absent_and_single_live_review_required"
    if readiness == "blocked_provider_or_budget_hold":
        reason = "provider_or_budget_gate_not_ready"
    elif readiness == "blocked_no_eligible_candidates":
        reason = "no_eligible_candidates"
    elif single_decision.get("status") == "go_one_manual_paid_call_only":
        reason = "single_live_result_not_reviewed"
    return {
        "status": status,
        "reason": reason,
        "readiness": readiness,
        "batch_execution_allowed": bool(batch_report.get("batch_execution_allowed")),
        "effective_target_count": _int(_as_dict(batch_report.get("targets")).get("effective_count")),
        "decision_counts": _as_dict(_as_dict(batch_report.get("targets")).get("decision_counts")),
        "blocker_counts": _as_dict(_as_dict(batch_report.get("targets")).get("blocker_counts")),
    }


def _evidence_only_decision(ai_report: dict[str, Any]) -> dict[str, Any]:
    summary = _as_dict(ai_report.get("summary"))
    passed = bool(ai_report.get("passed"))
    traceable = bool(_as_dict(ai_report.get("checks")).get("all_brief_items_traceable"))
    status = "go" if passed and traceable else "hold"
    return {
        "status": status,
        "reason": "traceable_existing_evidence_only" if status == "go" else "brief_traceability_not_ready",
        "brief_item_count": _int(summary.get("brief_item_count")),
        "next_action_count": _int(summary.get("next_action_count")),
        "evidence_backlink_count": _int(summary.get("evidence_backlink_count")),
        "sections": summary.get("sections") if isinstance(summary.get("sections"), list) else [],
    }


def _final_decision(evidence_decision: dict[str, Any], single_decision: dict[str, Any], batch_decision: dict[str, Any]) -> dict[str, Any]:
    if evidence_decision.get("status") != "go":
        return {
            "status": "hold_all_ai_surface",
            "reason": "evidence_brief_not_traceable",
            "go_next": [],
            "hold": ["single_live_gemini", "batch_gemini", "ai_brief"],
        }
    if single_decision.get("status") == "go_one_manual_paid_call_only":
        return {
            "status": "go_evidence_only_and_one_manual_live_call_hold_batch",
            "reason": "single_live_candidate_ready_but_batch_requires_reviewed_result",
            "go_next": ["evidence_only_ai_brief", "one_manual_paid_gemini_call_after_operator_approval"],
            "hold": ["batch_gemini"],
        }
    return {
        "status": "go_evidence_only_hold_live_and_batch",
        "reason": single_decision.get("reason") or batch_decision.get("reason") or "provider_or_budget_gate_not_ready",
        "go_next": ["evidence_only_ai_brief"],
        "hold": ["single_live_gemini", "batch_gemini"],
    }


def _side_effects(*reports: dict[str, Any]) -> dict[str, bool]:
    return {
        "provider_calls": any(bool(report.get("provider_calls")) for report in reports),
        "llm_calls": any(bool(report.get("llm_calls")) for report in reports),
        "write_db": any(bool(report.get("write_db")) for report in reports),
        "sync_triggered": any(bool(report.get("sync_triggered")) for report in reports),
        "task_enqueued": any(bool(report.get("task_enqueued")) for report in reports),
    }


def build_report(
    *,
    query: str = "viltrox",
    kol_pool_id: int = 0,
    kol_pool_ids: list[int] | None = None,
) -> dict[str, Any]:
    ids = [int(value) for value in (kol_pool_ids or []) if int(value or 0) > 0]
    if kol_pool_id and kol_pool_id not in ids:
        ids.insert(0, int(kol_pool_id))
    sample_id = ids[0] if ids else int(kol_pool_id or 0)
    budget_report = vkpi_llm_budget_acceptance.build_report(
        prompt="P4.61 acceptance: summarize existing evidence only; do not create new facts.",
        max_output_tokens=200,
    )
    go_no_go_report = vkpi_gemini_go_no_go_report.build_report(
        query=query,
        kol_pool_id=sample_id,
        candidate_limit=24,
    )
    batch_report = vkpi_gemini_batch30_dry_run.build_report(
        query=query,
        kol_pool_ids=ids or ([sample_id] if sample_id else []),
        target_size=30,
        window_size=99,
        requested_concurrency=9,
    )
    ai_report = vkpi_ai_brief_acceptance.build_report(
        query=query,
        kol_pool_id=sample_id,
        include_product_fit=True,
    )
    cost = _provider_estimates(budget_report)
    quality = {
        "single_kol_valid_video_url": bool(_as_dict(go_no_go_report.get("summary")).get("valid_video_url")),
        "single_kol_candidate_count": _int(_as_dict(go_no_go_report.get("summary")).get("candidate_count")),
        "ai_brief_item_count": _int(_as_dict(ai_report.get("summary")).get("brief_item_count")),
        "ai_brief_next_action_count": _int(_as_dict(ai_report.get("summary")).get("next_action_count")),
        "ai_brief_evidence_backlink_count": _int(_as_dict(ai_report.get("summary")).get("evidence_backlink_count")),
        "batch_effective_target_count": _int(_as_dict(batch_report.get("targets")).get("effective_count")),
    }
    trust = {
        "ai_brief_traceable": bool(_as_dict(ai_report.get("checks")).get("all_brief_items_traceable")),
        "next_actions_traceable": bool(_as_dict(ai_report.get("checks")).get("all_next_actions_traceable")),
        "new_fact_generation_disabled": bool(_as_dict(ai_report.get("checks")).get("new_fact_generation_disabled")),
        "recommendations_require_evidence": bool(_as_dict(ai_report.get("checks")).get("recommendations_require_evidence")),
        "batch_has_no_execution_commands": bool(_as_dict(batch_report.get("checks")).get("no_execution_commands")),
    }
    evidence_decision = _evidence_only_decision(ai_report)
    single_decision = _single_live_decision(go_no_go_report)
    batch_decision = _batch_decision(batch_report, single_decision)
    final_decision = _final_decision(evidence_decision, single_decision, batch_decision)
    effects = _side_effects(budget_report, go_no_go_report, batch_report, ai_report)
    checks = {
        "budget_report_readonly": not bool(budget_report.get("provider_calls")) and not bool(budget_report.get("write_db")),
        "gemini_go_no_go_readonly": bool(go_no_go_report.get("passed")) and not bool(go_no_go_report.get("provider_calls")),
        "batch_dry_run_readonly": bool(batch_report.get("passed")) and not bool(batch_report.get("batch_execution_allowed")),
        "ai_brief_acceptance_passed": bool(ai_report.get("passed")),
        "provider_calls_blocked": not effects["provider_calls"],
        "llm_calls_blocked": not effects["llm_calls"],
        "write_db_blocked": not effects["write_db"],
        "sync_blocked": not effects["sync_triggered"],
        "task_enqueue_blocked": not effects["task_enqueued"],
        "batch_execution_blocked": not bool(batch_report.get("batch_execution_allowed")),
        "single_live_or_gate_required": single_decision.get("status") in {"hold", "no_go_for_this_kol", "go_one_manual_paid_call_only"},
        "evidence_only_can_continue": evidence_decision.get("status") == "go",
        "all_next_actions_traceable": trust["next_actions_traceable"],
        "final_decision_recorded": bool(final_decision.get("status")),
    }
    return {
        "mode": "read_only_p4_61_llm_gemini_phase_acceptance",
        "generated_at": _now(),
        "query": query,
        "kol_pool_id": sample_id,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "cost": cost,
        "quality": quality,
        "trust": trust,
        "decisions": {
            "evidence_only": evidence_decision,
            "single_live_gemini": single_decision,
            "batch_gemini": batch_decision,
            "final": final_decision,
        },
        "source_reports": {
            "budget": {
                "passed": bool(budget_report.get("passed")),
                "provider_gate_reason": cost.get("provider_gate_reason"),
            },
            "gemini_go_no_go": go_no_go_report.get("summary"),
            "batch30_dry_run": {
                "passed": bool(batch_report.get("passed")),
                "readiness": batch_report.get("readiness"),
                "target_count": _int(_as_dict(batch_report.get("targets")).get("effective_count")),
            },
            "ai_brief": ai_report.get("summary"),
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    final = _as_dict(_as_dict(report.get("decisions")).get("final"))
    cost = _as_dict(report.get("cost"))
    quality = _as_dict(report.get("quality"))
    trust = _as_dict(report.get("trust"))
    lines = [
        "# V-KPI P4.61 LLM/Gemini Phase Acceptance",
        "",
        "Read-only second go/no-go report. It aggregates budget, single-KOL Gemini, batch dry-run, and AI Brief acceptance reports without calling providers or writing data.",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Passed: `{str(report['passed']).lower()}`",
        f"- KOL pool ID: `{report.get('kol_pool_id') or 'none'}`",
        f"- Final decision: `{final.get('status')}`",
        f"- Final reason: `{final.get('reason')}`",
        f"- Provider gate: `{cost.get('provider_gate_reason') or 'not_blocked'}`",
        f"- Min estimated single preflight cost: `${_float(cost.get('min_estimated_cost_usd')):.4f}`",
        "",
        "## Quality",
        "",
    ]
    for key, value in quality.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Trust", ""])
    for key, value in trust.items():
        lines.append(f"- `{key}`: `{str(bool(value)).lower()}`")
    lines.extend(["", "## Decisions", ""])
    for key, value in _as_dict(report.get("decisions")).items():
        if isinstance(value, dict):
            lines.append(f"- `{key}`: `{value.get('status')}` / `{value.get('reason')}`")
    lines.extend(["", "## Checks", ""])
    for key, value in report["checks"].items():
        lines.append(f"- `{key}`: `{str(bool(value)).lower()}`")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a read-only P4.61 LLM/Gemini phase acceptance report.")
    parser.add_argument("--query", default="viltrox")
    parser.add_argument("--kol-pool-id", type=int, default=0)
    parser.add_argument("--kol-pool-ids", default="", help="Comma-separated KOL pool ids for batch dry-run.")
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
        ids = _ids_from_text(args.kol_pool_ids)
        report = build_report(
            query=str(args.query or "viltrox"),
            kol_pool_id=max(0, int(args.kol_pool_id or 0)),
            kol_pool_ids=ids,
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
