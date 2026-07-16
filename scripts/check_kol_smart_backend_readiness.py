#!/usr/bin/env python3
"""Read-only readiness gate for KOL smart URL/search backend integration.

This script turns the lower-level audit snapshot into an operator-facing
checklist. It does not write DB rows, enqueue jobs, call providers, or touch
V6 Fit fields.
"""
from __future__ import annotations

from stdout_utils import out

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import audit_kol_smart_backend_state as audit  # noqa: E402


@dataclass(frozen=True)
class Gate:
    key: str
    status: str
    evidence: str
    next_step: str = ""


def _contains(path: str, *needles: str) -> bool:
    text = (ROOT / path).read_text(encoding="utf-8")
    return all(needle in text for needle in needles)


def _state() -> dict[str, Any]:
    audit._load_env()
    with audit._connect() as conn:
        state: dict[str, Any] = {
            "git": {
                "branch": audit._git_value("rev-parse", "--abbrev-ref", "HEAD"),
                "short_sha": audit._git_value("rev-parse", "--short", "HEAD"),
            },
            "deep_results": audit.deep_result_state(conn),
            "search": audit.search_state(conn),
            "queue": audit.queue_state(conn),
            "queue_load_smoke": audit.queue_load_smoke_state(),
            "tiktok_video_resolver": audit.tiktok_video_resolver_state(conn),
            "url_classifier": audit.url_classifier_state(conn, sample_limit=5),
        }
    state["score"] = audit.score_summary(state)
    return state


def build_gates(state: dict[str, Any]) -> list[Gate]:
    deep = state["deep_results"]
    search = state["search"]
    queue = state["queue"]
    queue_load_smoke = state.get("queue_load_smoke") if isinstance(state.get("queue_load_smoke"), dict) else {}
    tiktok_resolver = state.get("tiktok_video_resolver") if isinstance(state.get("tiktok_video_resolver"), dict) else {}
    score = state["score"]
    url_state = state["url_classifier"]
    missing_video_count = int(deep["missing_video_deep_count"] or 0)
    writable_video_missing = int(deep.get("missing_video_deep_writable_count") or 0)
    profile_ready_insert = int((deep.get("profile_llm_projection") or {}).get("ready_insert") or 0)
    materialization_has_pending_writes = writable_video_missing > 0 or profile_ready_insert > 0

    gates: list[Gate] = []
    gates.append(
        Gate(
            "url_video_profile_text_router",
            "pass" if _contains("backend/app/api/routers/vkpi_kol_pool.py", "/kol-smart-search", "/kol-url-deep-crawl", "/kol-recall") else "fail",
            "router exposes smart search, URL deep crawl, and recall endpoints",
        )
    )
    gates.append(
        Gate(
            "video_url_to_evidence_enqueue",
            "pass" if _contains("backend/app/domains/kol/url_deep_crawl.py", "_execute_existing_creator_video_flow", "_execute_new_creator_video_flow")
            and _contains("backend/app/domains/kol/video_evidence.py", "ensure_video_evidence_from_url") else "fail",
            "video URL execute paths call the shared idempotent evidence service",
        )
    )
    gates.append(
        Gate(
            "text_need_queue_pipeline",
            "pass" if _contains("backend/app/domains/kol/profile_discovery.py", "enqueue_smart_search_profile_advance", "kol_smart_search_profile_advance")
            and _contains("backend/app/workers/apify_jobs_worker.py", "_process_smart_search_profile_advance") else "fail",
            "plain-text needs can be queued and worker has a matching processor",
        )
    )
    gates.append(
        Gate(
            "ig_shortcode_identity_regression",
            "pass" if (ROOT / "tests/test_vkpi_kol_url_video_identity.py").exists() else "warn",
            "unit coverage exists for direct and username-prefixed Instagram shortcode URLs",
        )
    )
    gates.append(
        Gate(
            "text_pipeline_queue_contract",
            "pass" if (ROOT / "tests/test_vkpi_kol_smart_search_pipeline.py").exists() else "warn",
            "unit coverage exists for the smart text pipeline queue contract",
        )
    )
    gates.append(
        Gate(
            "video_deep_materialized",
            "pass" if missing_video_count == 0 else "ready_to_commit" if writable_video_missing else "warn",
            f"{deep['video_deep_ready']}/{deep['final_v1_ready']} final_v1 caches materialized; writable missing={deep.get('missing_video_deep_writable_count', 0)}",
            "Run the approved video deep --commit command when ready."
            if writable_video_missing
            else "Only non-extractable final_v1 rows remain; do not write a fake score.",
        )
    )
    projection = deep.get("profile_llm_projection") if isinstance(deep.get("profile_llm_projection"), dict) else {}
    gates.append(
        Gate(
            "profile_llm_materialized",
            "pass" if int(deep["profile_llm_ready"] or 0) > 0 else "ready_to_commit" if int(projection.get("ready_insert") or 0) else "fail",
            f"profile_llm_ready={deep['profile_llm_ready']}; ready_insert={projection.get('ready_insert', 0)}; skipped={projection.get('skipped', 0)}",
            "Run the approved profile_llm --commit command when ready."
            if int(projection.get("ready_insert") or 0)
            else "Profile-level extracts are already materialized; future runs would update existing rows only.",
        )
    )
    gates.append(
        Gate(
            "search_history_smoke",
            "pass" if int(search["search_sessions"] or 0) > 0 and int(search["search_session_items"] or 0) > 0 else "partial" if int(search["search_sessions"] or 0) > 0 else "missing",
            f"search_sessions={search['search_sessions']}; search_session_items={search['search_session_items']}",
            "Run one real smart text queue smoke after write permission is approved."
            if not int(search["search_sessions"] or 0)
            else "History table accepts sessions; item-producing search smoke is the next deeper validation."
            if not int(search["search_session_items"] or 0)
            else "History table accepts sessions and candidate item previews.",
        )
    )
    gates.append(
        Gate(
            "queue_visibility",
            "pass" if "active_total" in queue else "fail",
            f"task queue active_total={queue.get('active_total')}",
        )
    )
    gates.append(
        Gate(
            "url_classifier_data_quality",
            "warn" if int(url_state.get("platform_mismatch_count") or 0) else "pass",
            f"evidence_with_url={url_state.get('evidence_with_url')}; not_video={url_state.get('not_classified_as_video')}; platform_mismatch={url_state.get('platform_mismatch_count')}",
            "Remaining mismatches are data-quality cleanup, not core router failure.",
        )
    )
    gates.append(
        Gate(
            "history_video_crawl",
            "pass" if _contains(
                "backend/app/domains/kol/url_deep_crawl.py",
                "_execute_profile_history_video_evidence",
                "_profile_should_materialize_history_videos",
                "_profile_history_video_limit",
                "_filter_incremental_profile_videos",
                "url_profile_history_video_evidence_v1",
            ) else "missing",
            "account_deep/full_history can materialize profile videos as evidence with since-window cutoff",
            "Run a provider-backed profile execute on a chosen KOL to populate real history evidence."
            if _contains(
                "backend/app/domains/kol/url_deep_crawl.py",
                "_execute_profile_history_video_evidence",
                "_profile_should_materialize_history_videos",
                "_profile_history_video_limit",
                "_filter_incremental_profile_videos",
                "url_profile_history_video_evidence_v1",
            )
            else "Build account history crawl service after materialization/search smoke.",
        )
    )
    gates.append(
        Gate(
            "tiktok_video_resolver",
            "known_risk",
            (
                f"evidence={tiktok_resolver.get('evidence_total', 0)}; "
                f"final_v1_failed={tiktok_resolver.get('final_v1_failed', 0)}; "
                f"r2_assets={tiktok_resolver.get('r2_cached_tiktok_assets', 0)}; "
                f"evidence_r2_hits={tiktok_resolver.get('r2_external_id_hits', 0)}"
            ),
            "Handle as a separate resolver/R2-cache task; current failed evidence has no matching cached R2 media.",
        )
    )
    gates.append(
        Gate(
            "hundred_user_ordered_queue",
            "pass" if queue_load_smoke.get("pass") else "unverified",
            (
                f"users={queue_load_smoke.get('users', 0)}; "
                f"claimed={queue_load_smoke.get('claimed', 0)}; "
                f"ordered={queue_load_smoke.get('ordered', False)}; "
                f"provider_calls={queue_load_smoke.get('provider_calls_performed', False)}; "
                f"persistent_write={queue_load_smoke.get('persistent_write', False)}"
            )
            if queue_load_smoke.get("pass")
            else "queue design exists, but a 100-user load test has not been run",
            "No-provider temp-table queue smoke passed; real provider throughput remains governed by Worker/provider capacity."
            if queue_load_smoke.get("pass")
            else "Run scripts/smoke_kol_smart_queue_load.py --users 100 before claiming queue ordering capacity.",
        )
    )
    gates.append(
        Gate(
            "materialization_projection",
            "ready_to_commit" if materialization_has_pending_writes else "pass",
            f"current={score['materialized_data_layers_estimate']}%; projected={score.get('projected_materialized_data_layers_if_ready_backfills_committed')}%",
            "Materialization write candidates remain."
            if materialization_has_pending_writes
            else "No approved materialization write candidates remain.",
        )
    )
    return gates


def pending_write_commands(gates: list[Gate]) -> list[str]:
    keys = {gate.key for gate in gates if gate.status == "ready_to_commit"}
    commands: list[str] = []
    if "video_deep_materialized" in keys:
        commands.append("python3 scripts/backfill_kol_llm_deep_analysis_results.py --cache-id 266,267,268,269 --commit")
    if "profile_llm_materialized" in keys:
        commands.append("python3 scripts/backfill_kol_account_dossier_extract.py --commit")
    return commands


def print_report(state: dict[str, Any], gates: list[Gate]) -> None:
    score = state["score"]
    out("KOL Smart Backend Readiness")
    out(f"branch: {state['git']['branch']} sha: {state['git']['short_sha']}")
    out(f"code_chain_estimate: {score['code_chain_estimate']}")
    out(f"materialized_data_layers_estimate: {score['materialized_data_layers_estimate']}%")
    out(
        "projected_materialized_data_layers_if_ready_backfills_committed: "
        f"{score.get('projected_materialized_data_layers_if_ready_backfills_committed')}%"
    )
    out("gates:")
    for gate in gates:
        out(f"  [{gate.status}] {gate.key}: {gate.evidence}")
        if gate.next_step:
            out(f"       next: {gate.next_step}")
    commands = pending_write_commands(gates)
    if commands:
        out("approved_write_commands_pending_confirmation:")
        for command in commands:
            out(f"  {command}")
    else:
        out("approved_write_commands_pending_confirmation: none")


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only KOL smart backend readiness gate.")
    parser.add_argument("--json", action="store_true", help="Print JSON after the text report.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero while any gate is not pass/ready_to_commit.")
    args = parser.parse_args()

    state = _state()
    gates = build_gates(state)
    print_report(state, gates)
    if args.json:
        out("readiness_json:")
        out(
            json.dumps(
                {
                    "state_score": state["score"],
                    "gates": [asdict(gate) for gate in gates],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                default=str,
            )
        )
    if args.strict:
        allowed = {"pass", "ready_to_commit"}
        if any(gate.status not in allowed for gate in gates):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
