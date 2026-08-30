#!/usr/bin/env python3
"""Guarded local canary for the exact first-round KOL search.

Default execution is plan-only. A network call requires ``--execute``,
``--allow-provider-calls``, and ``--authorization <fresh plan_hash>`` together.
The live path is loopback PostgreSQL + YouTube only, with one or two exact
QueryCells and ten raw rows per cell. Apify, LLM, Gemini, fallback queries,
pagination, enrollment, queueing, and database writes are disabled.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
SCRIPTS = ROOT / "scripts"
OPS = Path(__file__).resolve().parent
for import_path in (BACKEND, SCRIPTS, OPS):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

# This support module imports stdlib only. No app/provider-ready module is
# imported until ``configure_runtime`` has cleared forbidden credentials.
from targeted_search_canary_support import (  # noqa: E402
    CANARY_SCHEMA,
    CLAIM_STATUS,
    DEFAULT_LOCAL_DATABASE_URL,
    DEFAULT_QUERY,
    FOLLOWER_BAND,
    MAX_QUERY_CELLS,
    RAW_LIMIT_PER_CELL,
    CanarySafetyError,
    authorization_reason,
    build_canary_plan,
    configure_runtime,
    count_delta,
    database_label,
    positive_int,
    provider_readiness,
    qualification_summary,
    safe_candidate,
    safe_cell_runs,
    start_read_only,
    table_counts,
    text,
    validate_loopback_database_url,
    youtube_usage,
)


Discover = Callable[..., Awaitable[dict[str, Any]]]
Planner = Callable[..., dict[str, Any]]
PolicyBuilder = Callable[..., dict[str, Any]]
Qualifier = Callable[..., dict[str, Any]]


async def _youtube_search_without_fallback(
    platform: str,
    query: str,
    *,
    market: str = "",
    max_results: int = RAW_LIMIT_PER_CELL,
    relevance_language: str = "en",
    strict_evidence: bool = False,
    enrich_prefilter: Any = None,
    deadline_seconds: float | None = None,
    page_cursor: Any = None,
    exact_query: bool = False,
) -> dict[str, Any]:
    """Call the YouTube fast path directly; Apify is unreachable by design."""

    del enrich_prefilter, deadline_seconds
    if (
        text(platform, limit=20).lower() != "youtube"
        or not strict_evidence
        or not exact_query
        or page_cursor not in (None, {})
    ):
        return {
            "status": "blocked_by_canary_policy",
            "platform": "youtube",
            "items": [],
            "message": "youtube_exact_strict_first_page_required",
        }
    from app.services.intelligence.account_search_discovery import (
        _youtube_data_api_strict_video_search,
    )

    result = await _youtube_data_api_strict_video_search(
        query,
        market=market,
        safe_limit=min(
            RAW_LIMIT_PER_CELL,
            max(1, int(max_results or RAW_LIMIT_PER_CELL)),
        ),
        relevance_language=relevance_language,
        page_cursor=None,
        exact_query=True,
    )
    return result or {
        "status": "provider_unavailable",
        "platform": "youtube",
        "items": [],
        "metadata": {
            "provider": "youtube_data_api",
            "youtube_search_calls": 0,
            "youtube_combined_quota_units": 0,
            "youtube_api_calls": 0,
            "quota_units": 0,
            "quota_units_deprecated": True,
            "query_mode": "exact_query_cell",
        },
        "message": "youtube_data_api_unavailable_no_fallback",
    }


async def _execute_default_discovery(
    *,
    query_cells: list[dict[str, Any]],
    base_kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Temporarily fence production discovery to the YouTube-only fast path."""

    from app.domains.kol import profile_discovery_provider
    from app.domains.kol.targeted_query_execution import execute_first_round_query_cells

    original_search = profile_discovery_provider.search_platform_content
    original_localize = profile_discovery_provider._localize_search_terms
    profile_discovery_provider.search_platform_content = _youtube_search_without_fallback
    profile_discovery_provider._localize_search_terms = lambda query, _language: text(query)
    try:
        return await execute_first_round_query_cells(
            query_cells=query_cells,
            base_kwargs=base_kwargs,
            discover=profile_discovery_provider.discover_new_creators,
        )
    finally:
        profile_discovery_provider.search_platform_content = original_search
        profile_discovery_provider._localize_search_terms = original_localize


async def _execute_injected_discovery(
    *,
    query_cells: list[dict[str, Any]],
    base_kwargs: dict[str, Any],
    discover: Discover,
) -> dict[str, Any]:
    from app.domains.kol.targeted_query_execution import execute_first_round_query_cells

    return await execute_first_round_query_cells(
        query_cells=query_cells,
        base_kwargs=base_kwargs,
        discover=discover,
    )


def _load_planner() -> Planner:
    from app.domains.kol.smart_query_planner import plan_text_query_provider_free

    return plan_text_query_provider_free


def _load_qualification() -> tuple[PolicyBuilder, Qualifier]:
    from app.domains.kol.profile_online_qualification import (
        online_policy,
        qualify_online_candidates,
    )

    return online_policy, qualify_online_candidates


def _base_discovery_kwargs(plan: dict[str, Any]) -> dict[str, Any]:
    search_inputs = plan["search_inputs"]
    return {
        "platforms": ["youtube"],
        "market": plan["market"],
        "product_focus": search_inputs["product_focus"],
        "ideal_creator_types": search_inputs["ideal_creator_types"],
        "verticals": search_inputs["verticals"],
        "avoid_types": search_inputs["avoid_types"],
        "target_persona": search_inputs["target_persona"],
        "exclude_chinese": True,
    }


def _dual_qualification(
    batch: dict[str, Any],
    plan: dict[str, Any],
    *,
    policy_builder: PolicyBuilder,
    qualify: Qualifier,
) -> dict[str, Any]:
    candidates = [
        dict(item)
        for item in batch.get("new_creators") or []
        if isinstance(item, dict)
    ]
    result: dict[str, Any] = {}
    for name, followers_min, followers_max in (
        ("followers_unlimited", None, None),
        ("followers_50k_500k", FOLLOWER_BAND[0], FOLLOWER_BAND[1]),
    ):
        policy = policy_builder(
            market=plan["market"],
            platforms=["youtube"],
            languages=None,
            profile_types=None,
            exclude_chinese=True,
            followers_min=followers_min,
            followers_max=followers_max,
            source="targeted_search_canary",
        )
        qualified = qualify(
            [dict(item) for item in candidates],
            query_text=plan["query"],
            policy=policy,
            local_canonical_keys=set(),
            search_brief=plan["search_brief"],
        )
        result[name] = qualification_summary(qualified)
    return result


def _execution_summary(
    batch: dict[str, Any],
    *,
    authorized: bool,
    requested: bool,
    reason: str,
) -> dict[str, Any]:
    usage = youtube_usage(batch) if authorized else {
        "youtube_search_calls": 0,
        "youtube_combined_quota_units": 0,
        "youtube_api_calls": 0,
    }
    discovery_legs = positive_int(batch.get("provider_call_count")) if authorized else 0
    return {
        "requested": requested,
        "authorized": authorized,
        "reason": reason or "authorized_plan_executed",
        "provider_calls": bool(batch.get("provider_calls")) if authorized else False,
        "discovery_leg_count": discovery_legs,
        "status": text(batch.get("status"), limit=80) if authorized else "not_executed",
        "query_mode": (
            text(batch.get("query_mode"), limit=80)
            if authorized else "targeted_first_round_exact"
        ),
        "query_cells_executed": positive_int(batch.get("query_cells_executed")) if authorized else 0,
        "raw_candidate_occurrences": positive_int(batch.get("raw_candidate_occurrences")) if authorized else 0,
        "unique_candidate_count": positive_int(batch.get("unique_candidate_count")) if authorized else 0,
        "fallback_queries_used": bool(batch.get("fallback_queries_used")) if authorized else False,
        **usage,
        "youtube_quota_units": usage["youtube_combined_quota_units"],
        "youtube_quota_units_deprecated": True,
        "query_cell_runs": safe_cell_runs(batch.get("query_cell_runs")) if authorized else [],
        "public_candidates": [
            safe_candidate(item)
            for item in (batch.get("new_creators") or [])[:20]
            if isinstance(item, dict)
        ] if authorized else [],
    }


async def run_from_args(
    args: argparse.Namespace,
    *,
    planner: Planner | None = None,
    discover: Discover | None = None,
    conn: Any = None,
    policy_builder: PolicyBuilder | None = None,
    qualify: Qualifier | None = None,
) -> dict[str, Any]:
    """Build a plan and execute only after every local safety gate passes."""

    database_url = validate_loopback_database_url(str(args.database_url or ""))
    configure_runtime(database_url)
    planner_fn = planner or _load_planner()
    connection = conn
    if connection is None:
        from app.db.connection import get_conn

        connection = get_conn()
    try:
        start_read_only(connection)
        before = table_counts(connection)
        plan = build_canary_plan(
            query=str(args.query or ""),
            market=str(args.market or "US"),
            product_sku=str(args.product_sku or ""),
            planner=planner_fn,
            cell_count=int(args.cell_count),
        )
        readiness = provider_readiness()
        reason = authorization_reason(args, plan["plan_hash"])
        if not reason and not readiness["youtube_data_api_configured"]:
            reason = "youtube_api_not_configured"
        if not all(
            readiness[key]
            for key in ("apify_disabled", "llm_disabled", "gemini_disabled")
        ):
            reason = "forbidden_provider_surface_not_disabled"
        authorized = not reason
        batch: dict[str, Any] = {}
        qualification: dict[str, Any] = {}
        if authorized:
            kwargs = {
                "query_cells": plan["query_cells"],
                "base_kwargs": _base_discovery_kwargs(plan),
            }
            batch = (
                await _execute_default_discovery(**kwargs)
                if discover is None
                else await _execute_injected_discovery(**kwargs, discover=discover)
            )
            policy_fn, qualify_fn = (
                (policy_builder, qualify)
                if policy_builder is not None and qualify is not None
                else _load_qualification()
            )
            qualification = _dual_qualification(
                batch,
                plan,
                policy_builder=policy_fn,
                qualify=qualify_fn,
            )
        after = table_counts(connection)
        delta = count_delta(before, after)
        mutations = any(value != 0 for value in delta.values())
        execution = _execution_summary(
            batch,
            authorized=authorized,
            requested=bool(args.execute or args.allow_provider_calls),
            reason=reason,
        )
        safety_passed = (
            not mutations
            and not execution["fallback_queries_used"]
            and 1 <= len(plan["query_cells"]) <= MAX_QUERY_CELLS
            and all(cell["raw_limit"] == RAW_LIMIT_PER_CELL for cell in plan["query_cells"])
            and all(cell["platforms"] == ["youtube"] for cell in plan["query_cells"])
            and execution["discovery_leg_count"] <= plan["execution_limits"]["max_discovery_legs"]
            and execution["youtube_search_calls"] <= plan["execution_limits"]["max_youtube_search_calls"]
            and execution["youtube_combined_quota_units"] <= plan["execution_limits"]["max_youtube_combined_quota_units"]
            and execution["youtube_api_calls"] <= plan["execution_limits"]["max_youtube_api_calls"]
        )
        return {
            "schema": CANARY_SCHEMA,
            "claim_status": CLAIM_STATUS,
            "mode": "authorized_live_canary" if authorized else "plan_only",
            "passed": safety_passed,
            "plan": plan,
            "provider_readiness": readiness,
            "execution": execution,
            "qualification": qualification,
            "database": {
                **database_label(database_url),
                "read_only_verified": True,
                "transaction": "read_only_rolled_back",
                "counts_before": before,
                "counts_after": after,
                "count_delta": delta,
                "mutations_detected": mutations,
            },
            "evidence_boundary": {
                "provider_observation": bool(execution["provider_calls"]),
                "qualification": "in_memory_descriptive_only" if authorized else "not_run",
                "accuracy_proven": False,
                "campaign_growth_proven": False,
                "conversion_proven": False,
                "cloud_deployed": False,
            },
        }
    finally:
        connection.rollback()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--product-sku", default="")
    parser.add_argument("--market", default="US")
    parser.add_argument(
        "--cell-count",
        type=int,
        choices=(1, 2),
        default=2,
        help="Run the first one or two authorized QueryCells (default: 2).",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get(
            "VKPI_TARGETED_CANARY_DATABASE_URL",
            os.environ.get("LOCAL_DATABASE_URL", DEFAULT_LOCAL_DATABASE_URL),
        ),
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-provider-calls", action="store_true")
    parser.add_argument(
        "--authorization",
        default="",
        help="Exact plan_hash printed by a fresh plan-only run.",
    )
    parser.add_argument("--json-out", default="")
    parser.add_argument("--compact", action="store_true")
    return parser.parse_args(argv)


def _render(payload: dict[str, Any], *, compact: bool) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        indent=None if compact else 2,
        separators=(",", ":") if compact else None,
    ) + "\n"


async def async_main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = await run_from_args(args)
        rendered = _render(report, compact=bool(args.compact))
        if args.json_out:
            path = Path(str(args.json_out)).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered, encoding="utf-8")
        sys.stdout.write(rendered)
        if report["execution"]["requested"] and not report["execution"]["authorized"]:
            return 3
        return 0 if report.get("passed") else 4
    except (CanarySafetyError, ValueError) as exc:
        reason = (
            exc.code
            if isinstance(exc, CanarySafetyError)
            else "loopback_postgresql_url_required"
        )
        sys.stdout.write(_render({
            "schema": CANARY_SCHEMA,
            "claim_status": CLAIM_STATUS,
            "mode": "blocked",
            "passed": False,
            "reason": reason,
            "provider_calls": False,
        }, compact=bool(args.compact)))
        return 2
    finally:
        connection_module = sys.modules.get("app.db.connection")
        close = getattr(connection_module, "close_db_runtime", None) if connection_module else None
        if close is not None:
            await close()


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
