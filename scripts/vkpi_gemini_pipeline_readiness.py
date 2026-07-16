#!/usr/bin/env python3
"""Read-only P4.57 Gemini pipeline readiness report.

This is a control-plane report only. It does not call Gemini, enqueue tasks,
run sync, or write database rows.
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
ADR_PATH = ROOT / "docs" / "vkpi" / "adr" / "2026-05-23-gemini-pipeline-control.md"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.connection import close_db_runtime  # noqa: E402
from app.domains.intelligence import gemini_single_kol_preflight  # noqa: E402
from app.domains.search import natural_search  # noqa: E402


MAX_BATCH_SIZE = 30
INITIAL_CONCURRENCY = 1
HARD_MAX_CONCURRENCY = 2
MAX_SAMPLE_KOLS = 10


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _int(value: Any) -> int:
    try:
        return int(float(str(value or "").replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


def _ids_from_text(value: str) -> list[int]:
    ids: list[int] = []
    for part in str(value or "").replace(";", ",").split(","):
        parsed = _int(part)
        if parsed > 0 and parsed not in ids:
            ids.append(parsed)
    return ids


def _candidate_ids_from_search(query: str, *, limit: int) -> list[int]:
    payload = natural_search.search(query, limit=max(1, min(50, int(limit or 10))))
    ids: list[int] = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict) or item.get("source_table") != "vkpi_kol_pool":
            continue
        kol_pool_id = _int(item.get("source_id"))
        if kol_pool_id > 0 and kol_pool_id not in ids:
            ids.append(kol_pool_id)
    return ids


def _decision_counts(samples: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(item.get("decision") or "unknown") for item in samples)
    return dict(sorted(counts.items()))


def _pipeline_policy(*, requested_batch_size: int, requested_concurrency: int) -> dict[str, Any]:
    effective_batch_size = max(1, min(MAX_BATCH_SIZE, int(requested_batch_size or MAX_BATCH_SIZE)))
    effective_concurrency = max(1, min(HARD_MAX_CONCURRENCY, int(requested_concurrency or INITIAL_CONCURRENCY)))
    return {
        "stage": "P4.57",
        "batch_execution_allowed": False,
        "executor_exists": False,
        "requires_single_live_result_before_batch": True,
        "requested_batch_size": int(requested_batch_size or MAX_BATCH_SIZE),
        "effective_batch_size": effective_batch_size,
        "max_batch_size": MAX_BATCH_SIZE,
        "requested_concurrency": int(requested_concurrency or INITIAL_CONCURRENCY),
        "effective_concurrency": effective_concurrency,
        "initial_concurrency": INITIAL_CONCURRENCY,
        "hard_max_concurrency": HARD_MAX_CONCURRENCY,
        "minimum_delay_seconds_between_starts": 60,
        "stop_conditions": {
            "errors_gte": 3,
            "error_rate_gte": 0.20,
            "budget_warning_or_hard_stop": True,
            "provider_quota_or_rate_limit": True,
        },
        "retry_policy": {
            "max_retries_per_kol": 2,
            "backoff_minutes": [15, 60],
            "retryable": ["network_timeout", "provider_5xx", "temporary_file_processing_failure"],
            "not_retryable": [
                "no_cached_video_candidates",
                "invalid_video_url",
                "provider_not_configured",
                "budget_hard_stop",
                "quota_hard_stop",
                "model_configuration_error",
            ],
        },
        "required_budget_scopes": [
            "monthly_total",
            "single_call",
            "provider:gemini",
            "cron:p4_gemini_single_kol",
            "cron:p4_gemini_batch_30_future",
        ],
    }


def _readiness(decisions: dict[str, int]) -> str:
    if decisions.get("go_manual_single_call", 0) > 0:
        return "single_call_candidate_ready_batch_blocked"
    if decisions.get("hold", 0) > 0:
        return "design_ready_provider_or_budget_hold"
    if decisions.get("no_go_for_this_kol", 0) > 0:
        return "design_ready_no_current_candidates"
    return "design_ready_no_samples"


def build_report(
    *,
    query: str = "viltrox",
    kol_pool_ids: list[int] | None = None,
    candidate_limit: int = 24,
    sample_limit: int = 5,
    requested_batch_size: int = 30,
    requested_concurrency: int = 1,
) -> dict[str, Any]:
    ids = list(dict.fromkeys(int(value) for value in (kol_pool_ids or []) if int(value or 0) > 0))
    if not ids:
        ids = _candidate_ids_from_search(query, limit=max(1, min(MAX_SAMPLE_KOLS, int(sample_limit or 5))))
    sample_cap = max(1, min(MAX_SAMPLE_KOLS, int(sample_limit or 5)))
    samples: list[dict[str, Any]] = []
    for kol_pool_id in ids[:sample_cap]:
        try:
            report = gemini_single_kol_preflight.build_kol_pool_gemini_go_no_go(
                int(kol_pool_id),
                candidate_limit=max(1, min(100, int(candidate_limit or 24))),
            )
        except LookupError as exc:
            report = {
                "kol_pool_id": int(kol_pool_id),
                "decision": "not_found",
                "decision_reason": str(exc),
                "provider_calls": False,
                "llm_calls": False,
                "write_db": False,
                "sync_triggered": False,
                "task_enqueued": False,
            }
        samples.append(
            {
                "kol_pool_id": int(kol_pool_id),
                "decision": report.get("decision") or "unknown",
                "decision_reason": report.get("decision_reason") or "",
                "blockers": report.get("blockers") if isinstance(report.get("blockers"), list) else [],
                "summary": report.get("summary") if isinstance(report.get("summary"), dict) else {},
                "provider_calls": bool(report.get("provider_calls")),
                "llm_calls": bool(report.get("llm_calls")),
                "write_db": bool(report.get("write_db")),
                "sync_triggered": bool(report.get("sync_triggered")),
                "task_enqueued": bool(report.get("task_enqueued")),
            }
        )
    decisions = _decision_counts(samples)
    policy = _pipeline_policy(
        requested_batch_size=requested_batch_size,
        requested_concurrency=requested_concurrency,
    )
    checks = {
        "adr_exists": ADR_PATH.exists(),
        "sample_limit_bounded": len(samples) <= sample_cap <= MAX_SAMPLE_KOLS,
        "batch_execution_blocked": bool(policy.get("batch_execution_allowed")) is False,
        "executor_absent": bool(policy.get("executor_exists")) is False,
        "single_live_required_before_batch": bool(policy.get("requires_single_live_result_before_batch")),
        "max_batch_size_bounded": _int(policy.get("effective_batch_size")) <= MAX_BATCH_SIZE,
        "concurrency_bounded": _int(policy.get("effective_concurrency")) <= HARD_MAX_CONCURRENCY,
        "retry_policy_defined": bool(policy.get("retry_policy", {}).get("max_retries_per_kol") == 2),
        "stop_conditions_defined": bool(policy.get("stop_conditions", {}).get("errors_gte") == 3),
        "provider_calls_blocked": not any(bool(item.get("provider_calls")) for item in samples),
        "llm_calls_blocked": not any(bool(item.get("llm_calls")) for item in samples),
        "write_db_blocked": not any(bool(item.get("write_db")) for item in samples),
        "sync_blocked": not any(bool(item.get("sync_triggered")) for item in samples),
        "task_enqueue_blocked": not any(bool(item.get("task_enqueued")) for item in samples),
    }
    return {
        "mode": "read_only_p4_57_gemini_pipeline_readiness",
        "generated_at": _now(),
        "query": query,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "passed": all(bool(value) for value in checks.values()),
        "readiness": _readiness(decisions),
        "checks": checks,
        "policy": policy,
        "sample": {
            "requested_ids": ids,
            "evaluated_count": len(samples),
            "decision_counts": decisions,
            "items": samples,
        },
        "artifacts": {
            "adr_path": str(ADR_PATH.relative_to(ROOT)),
        },
        "next_steps": [
            "Keep batch execution disabled.",
            "Run one paid KOL only after explicit operator approval and budget gate changes.",
            "Review cost, latency, returned fields, and evidence quality before creating any batch executor.",
            "Create a separate cron:p4_gemini_batch_30 budget scope before future 30-KOL work.",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    policy = report["policy"]
    sample = report["sample"]
    lines = [
        "# V-KPI P4.57 Gemini Pipeline Readiness",
        "",
        "Read-only control-plane report. It does not call Gemini, enqueue tasks, run sync, or write DB rows.",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Passed: `{str(report['passed']).lower()}`",
        f"- Readiness: `{report['readiness']}`",
        f"- Provider calls: `{str(report['provider_calls']).lower()}`",
        f"- LLM calls: `{str(report['llm_calls']).lower()}`",
        f"- Write DB: `{str(report['write_db']).lower()}`",
        f"- Batch execution allowed: `{str(policy['batch_execution_allowed']).lower()}`",
        f"- Effective batch size: `{policy['effective_batch_size']}`",
        f"- Effective concurrency: `{policy['effective_concurrency']}`",
        f"- Sample evaluated: `{sample['evaluated_count']}`",
        "",
        "## Decision Counts",
        "",
        f"`{sample['decision_counts']}`",
        "",
        "## Controls",
        "",
        f"- max batch size: `{policy['max_batch_size']}`",
        f"- hard max concurrency: `{policy['hard_max_concurrency']}`",
        f"- min delay seconds: `{policy['minimum_delay_seconds_between_starts']}`",
        f"- max retries per KOL: `{policy['retry_policy']['max_retries_per_kol']}`",
        f"- stop errors >= `{policy['stop_conditions']['errors_gte']}`",
        f"- stop error rate >= `{policy['stop_conditions']['error_rate_gte']}`",
        "",
        "## Checks",
        "",
    ]
    for key, value in report["checks"].items():
        lines.append(f"- `{key}`: `{str(bool(value)).lower()}`")
    lines.extend(["", "## Next Steps", ""])
    lines.extend(f"- {item}" for item in report["next_steps"])
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a read-only P4.57 Gemini pipeline readiness report.")
    parser.add_argument("--query", default="viltrox")
    parser.add_argument("--kol-pool-ids", default="", help="Comma-separated KOL pool ids. If omitted, natural search provides samples.")
    parser.add_argument("--candidate-limit", type=int, default=24)
    parser.add_argument("--sample-limit", type=int, default=5)
    parser.add_argument("--requested-batch-size", type=int, default=30)
    parser.add_argument("--requested-concurrency", type=int, default=1)
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
            kol_pool_ids=_ids_from_text(args.kol_pool_ids),
            candidate_limit=max(1, min(100, int(args.candidate_limit or 24))),
            sample_limit=max(1, min(MAX_SAMPLE_KOLS, int(args.sample_limit or 5))),
            requested_batch_size=max(1, int(args.requested_batch_size or MAX_BATCH_SIZE)),
            requested_concurrency=max(1, int(args.requested_concurrency or INITIAL_CONCURRENCY)),
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
