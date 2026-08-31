"""Strict, server-owned qualification for the online net-new KOL lane.

Raw provider rows are evaluated only in memory.  A row is materialized into the
KOL pool and a search-session card only after all eight strict-v2 gates pass.
Missing/failed rows are represented by aggregate counters, never raw payloads.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
import hashlib
import inspect
import json
import re
from time import perf_counter
from typing import Any

from app.domains.kol import (
    profile_online_identity,
    profile_online_inventory,
    profile_recall_qualification,
)
from app.domains.kol.profile_online_evidence import (
    _CONTENT_EVIDENCE_LIMITS,
    _MAX_MATCHED_QUERY_CELLS,
    _ONLINE_PUBLIC_EVIDENCE_FIELDS,
    _PRIVATE_EVIDENCE_TERM_RE,
    _bounded_content_text,
    _candidate_query_cells,
    _candidate_row,
    _latest_video_evidence,
    _looks_like_video_url,
    _merge_match_evidence,
    _profile_url,
    _project_online_match_evidence,
    _representative_content_evidence,
    _safe_query_cell,
    _text,
    adapt_candidates as _adapt_candidates_impl,
    identity_probe as _identity_probe,
)
from app.domains.kol.profile_query_cell_evidence import build_query_cell_match_evidence
from app.domains.kol.profile_online_qualification_internal import (
    build_outcomes as _build_online_outcomes,
    mark_pending_content as _mark_pending_content,
    rewrite_pending_counts as _rewrite_pending_counts,
    search_objective as _search_objective,
)
from app.domains.kol.profile_online_growth import (
    _GROWTH_OUTPUT_FIELDS,
    _apply_prospective_growth_cell_scoring,
    _growth_cell_summary,
    activation_calibration_ids,
    surface_growth_gate_reasons,
)
from app.domains.kol.profile_recall_match_evidence import (
    why_fit_from_match_evidence,
)


ONLINE_TARGET = 30
ONLINE_SCHEMA = "smart_online_net_new_qualified_v1"
ONLINE_POLICY_VERSION = 1
ONLINE_ORIGIN_LANE = "online"
ONLINE_SOURCE = "platform_discovery_strict"
ONLINE_ITEM_TYPE = "online_qualified_candidate"
ONLINE_CANDIDATE_BUDGET = 150
ONLINE_MAX_PROVIDER_ROUNDS = 3
ONLINE_SUPPORTED_PLATFORMS = frozenset({"youtube", "instagram", "tiktok"})

_PENDING_REASONS = frozenset({
    "followers_unknown",
    "latest_video_unknown",
    "latest_video_identity_missing",
    "market_unknown",
    "language_unknown",
    "profile_type_unknown",
    "platform_unknown",
    "pending_content_evidence",
    "market_activation_missing",
    "insufficient_sample",
})

FetchBatch = Callable[..., Awaitable[dict[str, Any]] | dict[str, Any]]
EnrollCandidate = Callable[[dict[str, Any]], Awaitable[Any] | Any]


def online_policy(
    *,
    market: Any = "",
    platforms: Any = None,
    languages: Any = None,
    profile_types: Any = None,
    exclude_chinese: bool = True,
    followers_min: Any = None,
    followers_max: Any = None,
    source: Any = "operator",
    unknown_policy: Any = profile_recall_qualification.FOLLOWERS_UNKNOWN_PENDING,
) -> dict[str, Any]:
    """Build the immutable online policy by extending the local strict policy."""
    policy = profile_recall_qualification.smart_local_policy(
        market=market,
        platforms=platforms,
        languages=languages,
        profile_types=profile_types,
    )
    unsupported = sorted(set(policy.get("platforms") or []) - ONLINE_SUPPORTED_PLATFORMS)
    if unsupported:
        raise ValueError(f"unsupported strict online platforms: {', '.join(unsupported)}")
    follower_filter = profile_recall_qualification.follower_filter_policy(
        followers_min=followers_min,
        followers_max=followers_max,
        source=source,
        unknown_policy=unknown_policy,
    )
    policy.update({
        "origin_lane": ONLINE_ORIGIN_LANE,
        "online_schema": ONLINE_SCHEMA,
        "online_policy_version": ONLINE_POLICY_VERSION,
        "target_count": ONLINE_TARGET,
        "require_trusted_market": bool(policy.get("market")),
        "supported_platforms": sorted(ONLINE_SUPPORTED_PLATFORMS),
        "exclude_chinese_regions": bool(exclude_chinese),
        # Online discovery has no implicit reach floor.  A follower gate exists
        # only when the operator/planner supplies an explicit bound.
        "followers_filter": follower_filter,
        "min_followers": follower_filter["minimum"],
        "max_followers": follower_filter["maximum"],
        "allow_unknown_followers": not follower_filter["requested"],
        "evidence_sources": {
            "followers": "online_provider.followers",
            "language": "online_provider.language",
            "profile_type": "online_provider.profile_type",
            "platform": "online_provider.platform",
        },
    })
    return policy


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
def _cell_match_evidence(
    row: dict[str, Any],
    evidence: dict[str, Any],
    *,
    query_text: str,
    query_cell: dict[str, Any],
) -> list[dict[str, str]]:
    return build_query_cell_match_evidence(
        row,
        evidence,
        query_text,
        query_cell=query_cell,
        min_intent_terms=1,
    )


def _adapt_candidates(
    candidates: list[dict[str, Any]],
    *,
    query_text: str,
) -> tuple[
    list[dict[str, Any]],
    dict[int, dict[str, Any]],
    dict[int, dict[str, Any]],
    dict[int, dict[str, Any]],
    dict[int, list[dict[str, Any]]],
]:
    """Preserve the original adapter contract around the local evidence callback."""

    return _adapt_candidates_impl(
        candidates,
        query_text=query_text,
        cell_match_evidence=_cell_match_evidence,
    )


def _project_online_item(item: dict[str, Any]) -> dict[str, Any]:
    safe_match_evidence = _project_online_match_evidence(item.get("match_evidence"))
    projected = profile_recall_qualification.project_smart_local_result({
        "items": [item],
        "buckets": {},
        "local_qualification": {"schema": profile_recall_qualification.SMART_LOCAL_SCHEMA},
    })["items"][0]
    projected["origin_lane"] = ONLINE_ORIGIN_LANE
    projected["source"] = ONLINE_SOURCE
    projected["qualification_status"] = "accepted"
    projected["canonical_fingerprint"] = profile_online_identity.canonical_fingerprint(item)
    projected["kol_pool_id"] = None
    projected["match_evidence"] = safe_match_evidence
    projected["why_fit"] = why_fit_from_match_evidence(safe_match_evidence)
    proof = dict(projected.get("qualification_evidence") or {})
    proof.pop("kol_pool_id", None)
    relevance = proof.get("relevance") if isinstance(proof.get("relevance"), dict) else {}
    proof["relevance"] = {
        **relevance,
        "passed": bool(safe_match_evidence),
        "evidence": safe_match_evidence,
    }
    projected["qualification_evidence"] = proof
    return projected


def _qualify_online_candidates_internal(
    candidates: list[dict[str, Any]],
    *,
    query_text: str,
    policy: dict[str, Any],
    local_canonical_keys: set[str],
    remaining: int,
    search_brief: dict[str, Any] | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    adapted, rows, evidence, sources, cell_inputs = _adapt_candidates(
        candidates,
        query_text=query_text,
    )
    brief = search_brief if isinstance(search_brief, dict) else {}
    objective = _search_objective(brief, cell_inputs, text=_text)
    qualification_stats = {
        "unique_candidate_count": len(adapted),
        "cell_evaluation_count": 0,
        "qualified_cell_count": 0,
        "candidate_with_qualified_cell_count": 0,
        "multi_cell_candidate_count": 0,
    }
    if objective == "prospective_growth":
        calibration_ids = activation_calibration_ids(
            adapted,
            rows=rows,
            evidence=evidence,
            policy=policy,
            local_canonical_keys=local_canonical_keys,
            as_of=as_of,
            target_count=ONLINE_TARGET,
        )
        qualification_stats = _apply_prospective_growth_cell_scoring(
            adapted,
            cell_inputs_by_id=cell_inputs,
            search_brief=brief,
            activation_calibration_ids=calibration_ids,
        )
    selected, _, strict_contract = profile_recall_qualification.qualify_local_candidates(
        buckets={
            "creator": [item for item in adapted if item.get("bucket") != "reviewer"],
            "reviewer": [item for item in adapted if item.get("bucket") == "reviewer"],
        },
        rows_by_id=rows,
        evidence_by_id=evidence,
        policy=policy,
        creator_quota=min(max(0, remaining), ONLINE_TARGET),
        reviewer_quota=0,
        target_count=remaining,
        excluded_identity_aliases=local_canonical_keys,
        identity_aliases_fn=profile_recall_qualification.canonical_creator_aliases,
        excluded_identity_reason="duplicate_local_identity",
        as_of=as_of,
    )
    pending_content_ids = _mark_pending_content(adapted)
    _rewrite_pending_counts(strict_contract, pending_content_ids)
    surface_growth_gate_reasons(adapted, strict_contract)
    outcomes = _build_online_outcomes(
        adapted,
        selected,
        sources,
        pending_reasons=_PENDING_REASONS,
        canonical_creator_key=profile_recall_qualification.canonical_creator_key,
        project_online_item=_project_online_item,
    )
    return {
        "outcomes": outcomes,
        "strict_contract": strict_contract,
        "qualification_stats": qualification_stats,
    }


def qualify_online_candidates(
    candidates: list[dict[str, Any]],
    *,
    query_text: str,
    policy: dict[str, Any],
    local_canonical_keys: set[str] | None = None,
    search_brief: dict[str, Any] | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Provider-free strict qualification helper used by tests and orchestration."""
    result = _qualify_online_candidates_internal(
        [dict(item) for item in candidates if isinstance(item, dict)],
        query_text=query_text,
        policy=policy,
        local_canonical_keys=set(local_canonical_keys or set()),
        remaining=ONLINE_TARGET,
        search_brief=search_brief,
        as_of=as_of,
    )
    outcomes = result["outcomes"]
    accepted = [item["item"] for item in outcomes if item["status"] == "selected"]
    counts: dict[str, int] = {}
    for outcome in outcomes:
        counts[outcome["status"]] = counts.get(outcome["status"], 0) + 1
    qualification_stats = dict(result.get("qualification_stats") or {})
    return {
        "schema": ONLINE_SCHEMA,
        "origin_lane": ONLINE_ORIGIN_LANE,
        "accepted": accepted,
        "counts": counts,
        "rejected_by_reason": dict(result["strict_contract"].get("rejected_by_reason") or {}),
        "qualification_stats": qualification_stats,
        "unique_candidate_count": int(qualification_stats.get("unique_candidate_count") or 0),
        "cell_evaluation_count": int(qualification_stats.get("cell_evaluation_count") or 0),
    }


def _provider_candidates(result: dict[str, Any]) -> list[dict[str, Any]]:
    if "new_creators" in result:
        return [
            dict(item)
            for item in (result.get("new_creators") or [])
            if isinstance(item, dict)
        ]
    items = result.get("items")
    if isinstance(items, list):
        return [dict(item) for item in items if isinstance(item, dict)]
    return []


def _inventory_match(raw: dict[str, Any]) -> bool:
    historical = raw.get("historical_match") if isinstance(raw.get("historical_match"), dict) else {}
    return bool(
        _positive_int(raw.get("history_kol_pool_id"))
        or _positive_int(raw.get("kol_pool_id"))
        or _positive_int(historical.get("kol_pool_id"))
    )


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _enrolled_pool_id(value: Any) -> int | None:
    if isinstance(value, dict):
        return _positive_int(value.get("kol_pool_id") or value.get("id"))
    return _positive_int(value)


def _snapshot_id(
    items: list[dict[str, Any]],
    contract_core: dict[str, Any],
    *,
    query_text: str,
    policy: dict[str, Any],
) -> str:
    payload = {
        "canonical_fingerprints": [item.get("canonical_fingerprint") for item in items],
        "counts": contract_core,
        "query_text": _text(query_text),
        "policy": policy,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


async def collect_strict_online_candidates(
    *,
    query_text: str,
    policy: dict[str, Any],
    local_canonical_keys: set[str],
    inventory_aliases: set[str] | None = None,
    local_unique_count: int | None = None,
    inventory_snapshot_rows: int = 0,
    inventory_db_reads: int = 0,
    fetch_batch: FetchBatch,
    enroll_candidate: EnrollCandidate,
    candidate_budget: int = ONLINE_CANDIDATE_BUDGET,
    max_provider_rounds: int = ONLINE_MAX_PROVIDER_ROUNDS,
    round_gate: Callable[[int], dict[str, Any]] | None = None,
    exhaustion_reason: str = "bounded_provider_batch_exhausted",
    search_brief: dict[str, Any] | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Collect 30 strict candidates; a denied later-round gate never implies exhaustion."""
    started = perf_counter()
    budget = max(ONLINE_TARGET, min(int(candidate_budget or ONLINE_CANDIDATE_BUDGET), 500))
    max_rounds = max(1, min(int(max_provider_rounds or 1), 10))
    accepted: list[dict[str, Any]] = []
    accepted_aliases: set[str] = set()
    inventory_alias_set = set(inventory_aliases or set())
    local_rank_base = (
        max(0, min(int(local_unique_count), ONLINE_TARGET)) if local_unique_count is not None
        else min(ONLINE_TARGET, len(local_canonical_keys))
    )
    rejected_by_reason: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    provider_rounds = provider_calls = evaluated = 0
    unique_evaluated_keys: set[str] = set()
    multi_cell_evaluated_keys: set[str] = set()
    cell_evaluation_count = qualified_cell_count = budget_used = materialization_db_reads = 0
    cursor: Any = None
    provider_failed, has_more = False, True
    seen_batch_fingerprints: set[str] = set()
    gate_verdicts: list[dict[str, Any]] = []
    gate_stop_reason = ""

    def _round_allowed() -> bool:
        nonlocal gate_stop_reason
        if provider_rounds < 1 or round_gate is None:
            return True
        verdict = round_gate(provider_rounds + 1)
        verdict = verdict if isinstance(verdict, dict) else {}
        gate_verdicts.append(verdict)
        if verdict.get("allowed") is True:
            return True
        gate_stop_reason = _text(verdict.get("reason")) or "round_gate_denied"
        return False

    async def _fetch_round() -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
        nonlocal budget_used, evaluated, has_more, provider_calls, provider_failed, provider_rounds
        provider_rounds += 1
        request_limit = min(150, budget - budget_used)
        try:
            provider_result = await _maybe_await(fetch_batch(
                round_no=provider_rounds, limit=request_limit, cursor=cursor,
            ))
        except Exception:
            provider_failed = True
            status_counts["provider_failed"] = status_counts.get("provider_failed", 0) + 1
            return None
        provider_result = provider_result if isinstance(provider_result, dict) else {}
        if provider_result.get("provider_calls") is not False:
            try:
                reported_calls = int(provider_result.get("provider_call_count") or 1)
            except (TypeError, ValueError):
                reported_calls = 1
            provider_calls += max(1, min(reported_calls, 100))
        batch = _provider_candidates(provider_result)[:request_limit]
        budget_used += len(batch)
        evaluated += len(batch)
        fingerprint = hashlib.sha256(json.dumps([
            profile_recall_qualification.canonical_creator_key(item) for item in batch
        ], sort_keys=True).encode("utf-8")).hexdigest()
        if fingerprint in seen_batch_fingerprints:
            status_counts["duplicate_batch"] = status_counts.get("duplicate_batch", 0) + len(batch)
            has_more = False
            return None
        seen_batch_fingerprints.add(fingerprint)
        return provider_result, batch

    def _fresh_candidates(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        fresh: list[dict[str, Any]] = []
        for raw in batch:
            if _inventory_match(raw):
                status_counts["duplicate_local_inventory"] = status_counts.get("duplicate_local_inventory", 0) + 1
                continue
            aliases = profile_recall_qualification.canonical_creator_aliases(_identity_probe(raw))
            if aliases.intersection(accepted_aliases):
                status_counts["duplicate_online"] = status_counts.get("duplicate_online", 0) + 1
            elif aliases.intersection(inventory_alias_set) and not aliases.intersection(local_canonical_keys):
                status_counts["duplicate_local_inventory"] = status_counts.get("duplicate_local_inventory", 0) + 1
            else:
                fresh.append(raw)
        return fresh

    def _record_qualification(qualified: dict[str, Any], fresh: list[dict[str, Any]]) -> None:
        nonlocal cell_evaluation_count, qualified_cell_count
        qualification_stats = qualified.get("qualification_stats") or {}
        cell_evaluation_count += int(qualification_stats.get("cell_evaluation_count") or 0)
        qualified_cell_count += int(qualification_stats.get("qualified_cell_count") or 0)
        for raw in fresh:
            canonical = profile_recall_qualification.canonical_creator_key(_identity_probe(raw))
            if canonical:
                unique_evaluated_keys.add(canonical)
                if len(_candidate_query_cells(raw, query_text=query_text)) > 1:
                    multi_cell_evaluated_keys.add(canonical)
        for reason, count in (qualified["strict_contract"].get("rejected_by_reason") or {}).items():
            rejected_by_reason[str(reason)] = rejected_by_reason.get(str(reason), 0) + int(count or 0)

    async def _materialize_outcome(outcome: dict[str, Any]) -> None:
        nonlocal materialization_db_reads
        source = outcome.get("source") if isinstance(outcome.get("source"), dict) else {}
        try:
            materialized = await _maybe_await(enroll_candidate(source))
        except Exception:
            status_counts["rejected"] = status_counts.get("rejected", 0) + 1
            rejected_by_reason["enrollment_failed"] = rejected_by_reason.get("enrollment_failed", 0) + 1
            return
        if isinstance(materialized, dict):
            materialization_db_reads += max(0, int(materialized.get("db_reads") or 0))
        if isinstance(materialized, dict) and (
            materialized.get("duplicate_local_inventory") is True
            or materialized.get("matched_existing") is True
        ):
            status_counts["duplicate_local_inventory"] = status_counts.get("duplicate_local_inventory", 0) + 1
            return
        kol_pool_id = _enrolled_pool_id(materialized)
        if not kol_pool_id:
            status_counts["rejected"] = status_counts.get("rejected", 0) + 1
            rejected_by_reason["enrollment_failed"] = rejected_by_reason.get("enrollment_failed", 0) + 1
            return
        item = dict(outcome["item"] or {})
        proof = dict(item.get("qualification_evidence") or {})
        identity_fingerprint = _text(item.get("canonical_fingerprint"))
        if not re.fullmatch(r"[0-9a-f]{64}", identity_fingerprint):
            status_counts["rejected"] = status_counts.get("rejected", 0) + 1
            rejected_by_reason["identity_fingerprint_missing"] = (
                rejected_by_reason.get("identity_fingerprint_missing", 0) + 1
            )
            return
        proof.update({"kol_pool_id": kol_pool_id, "canonical_fingerprint": identity_fingerprint})
        item.update({
            "kol_pool_id": kol_pool_id,
            "qualification_evidence": proof,
            "server_rank": len(accepted) + 1,
            "global_unique_rank": local_rank_base + len(accepted) + 1,
            "duplicate_of_lane": None,
            "contact_preview": {"status": "not_enriched", "channel_count": 0},
            "analysis_preview": {"status": "not_enriched", "async": False},
        })
        aliases = profile_recall_qualification.canonical_creator_aliases(item)
        if aliases.intersection(accepted_aliases):
            status_counts["duplicate_online"] = status_counts.get("duplicate_online", 0) + 1
            return
        accepted_aliases.update(aliases)
        accepted.append(item)
        status_counts["accepted"] = status_counts.get("accepted", 0) + 1

    async def _consume_outcomes(outcomes: list[dict[str, Any]]) -> None:
        for outcome in outcomes:
            outcome_status = str(outcome["status"])
            if outcome_status not in {"selected", "qualified_overflow"}:
                status_counts[outcome_status] = status_counts.get(outcome_status, 0) + 1
                continue
            if len(accepted) >= ONLINE_TARGET:
                status_counts["qualified_overflow"] = status_counts.get("qualified_overflow", 0) + 1
                continue
            await _materialize_outcome(outcome)
            if len(accepted) >= ONLINE_TARGET:
                break

    def _finish_round(provider_result: dict[str, Any], batch: list[dict[str, Any]]) -> None:
        nonlocal cursor, has_more, provider_failed
        cursor = provider_result.get("next_cursor")
        has_more = bool(provider_result.get("has_more") and cursor)
        if provider_result.get("status") == "failed" and not batch:
            provider_failed = True
            status_counts["provider_failed"] = status_counts.get("provider_failed", 0) + 1
            has_more = False

    while len(accepted) < ONLINE_TARGET and has_more and provider_rounds < max_rounds and budget_used < budget:
        if not _round_allowed():
            break
        fetched = await _fetch_round()
        if fetched is None:
            break
        provider_result, batch = fetched
        fresh_batch = _fresh_candidates(batch)
        qualified = _qualify_online_candidates_internal(
            fresh_batch,
            query_text=query_text,
            policy=policy,
            local_canonical_keys=local_canonical_keys,
            remaining=max(1, ONLINE_TARGET - len(accepted)),
            search_brief=search_brief,
            as_of=as_of,
        )
        _record_qualification(qualified, fresh_batch)
        await _consume_outcomes(qualified["outcomes"])
        _finish_round(provider_result, batch)

    def _shortfall_details() -> tuple[int, dict[str, int]]:
        shortfall = max(0, ONLINE_TARGET - len(accepted))
        shortfall_reasons = dict(rejected_by_reason)
        reasons = (
            "pending", "rejected", "duplicate_local", "duplicate_local_inventory",
            "duplicate_online", "duplicate_batch",
        )
        for reason in reasons:
            count = status_counts.get(reason, 0)
            if count:
                shortfall_reasons[reason] = shortfall_reasons.get(reason, 0) + count
        if shortfall:
            terminal_reason = (
                "provider_failed" if provider_failed
                else gate_stop_reason if gate_stop_reason
                else "candidate_budget_exhausted" if budget_used >= budget
                else "provider_round_budget_exhausted" if has_more and provider_rounds >= max_rounds
                else _text(exhaustion_reason) or "bounded_provider_batch_exhausted"
            )
            shortfall_reasons[terminal_reason] = shortfall_reasons.get(terminal_reason, 0) + shortfall
        return shortfall, shortfall_reasons

    def _finalize() -> dict[str, Any]:
        shortfall, shortfall_reasons = _shortfall_details()
        core_counts = {
            "target_count": ONLINE_TARGET, "evaluated_count": evaluated,
            "unique_evaluated_count": len(unique_evaluated_keys),
            "cell_evaluation_count": cell_evaluation_count, "returned_count": len(accepted),
            "shortfall": shortfall, "provider_rounds": provider_rounds,
            "candidate_budget_used": budget_used,
            "inventory_snapshot_rows": max(0, int(inventory_snapshot_rows or 0)),
            "inventory_db_reads": max(0, int(inventory_db_reads or 0)),
            "materialization_db_reads": materialization_db_reads,
        }
        snapshot_id = _snapshot_id(accepted, core_counts, query_text=query_text, policy=policy)
        for item in accepted:
            item["snapshot_revision"] = provider_rounds
            item["snapshot_id"] = snapshot_id
            proof = dict(item.get("qualification_evidence") or {})
            proof.update({
                "snapshot_revision": provider_rounds, "snapshot_id": snapshot_id,
                "server_rank": item.get("server_rank"),
                "global_unique_rank": item.get("global_unique_rank"),
            })
            item["qualification_evidence"] = proof
        return {
            "schema": ONLINE_SCHEMA,
            "policy_version": ONLINE_POLICY_VERSION,
            "server_owned": True,
            "origin_lane": ONLINE_ORIGIN_LANE,
            "source": ONLINE_SOURCE,
            "policy": {
                "policy_version": ONLINE_POLICY_VERSION, "target_count": ONLINE_TARGET,
                "min_followers": policy.get("min_followers"),
                "max_followers": policy.get("max_followers"),
                "followers_filter": dict(policy.get("followers_filter") or {}),
                "max_video_age_days": policy.get("max_video_age_days"),
                "market": policy.get("market"),
                "platforms": list(policy.get("platforms") or []),
                "languages": list(policy.get("languages") or []),
                "profile_types": list(policy.get("profile_types") or []),
                "supported_platforms": sorted(ONLINE_SUPPORTED_PLATFORMS),
                "exclude_chinese_regions": policy.get("exclude_chinese_regions") is True,
            },
            "query": {"query_text": _text(query_text)[:500], "source": "server_effective_query"},
            "status": "ready" if not shortfall else "shortfall",
            "terminal": True,
            "snapshot_complete": True,
            "snapshot_revision": max(1, provider_rounds),
            "snapshot_id": snapshot_id,
            "target_count": ONLINE_TARGET,
            "evaluated_count": evaluated,
            "unique_evaluated_count": len(unique_evaluated_keys),
            "cell_evaluation_count": cell_evaluation_count,
            "qualified_cell_count": qualified_cell_count,
            "multi_cell_candidate_count": len(multi_cell_evaluated_keys),
            "strict_qualified_count": sum(status_counts.get(key, 0) for key in (
                "accepted", "qualified_overflow", "duplicate_local", "duplicate_online",
            )),
            "net_new_accepted_count": len(accepted),
            "returned_count": len(accepted),
            "pending_count": status_counts.get("pending", 0),
            "rejected_count": status_counts.get("rejected", 0),
            "qualified_overflow_count": status_counts.get("qualified_overflow", 0),
            "duplicate_local_count": status_counts.get("duplicate_local", 0),
            "duplicate_local_inventory_count": status_counts.get("duplicate_local_inventory", 0),
            "duplicate_online_count": status_counts.get("duplicate_online", 0) + status_counts.get("duplicate_batch", 0),
            "provider_rounds": provider_rounds, "provider_calls": provider_calls,
            "candidate_budget": budget, "candidate_budget_used": budget_used,
            "inventory_snapshot_rows": max(0, int(inventory_snapshot_rows or 0)),
            "inventory_db_reads": max(0, int(inventory_db_reads or 0)),
            "materialization_db_reads": materialization_db_reads,
            "total_identity_db_reads": max(0, int(inventory_db_reads or 0)) + materialization_db_reads,
            "exhausted": not has_more,
            "round_gate": {"stopped_by": gate_stop_reason or None, "verdicts": gate_verdicts},
            "shortfall": shortfall,
            "shortfall_reasons": shortfall_reasons,
            "rejected_by_reason": rejected_by_reason,
            "stage_timing": {"online_qualification_ms": round((perf_counter() - started) * 1000.0, 3)},
            "items": accepted,
            "provider_calls_performed": provider_calls > 0,
            "viltrox_fit_score_untouched": True,
        }

    return _finalize()


local_identity_snapshot_for_session = profile_online_inventory.local_identity_snapshot_for_session
local_canonical_keys_for_session = profile_online_inventory.local_canonical_keys_for_session
inventory_alias_snapshot = profile_online_inventory.inventory_alias_snapshot
materialize_online_candidate = profile_online_inventory.materialize_online_candidate


async def collect_strict_online_for_session(
    *,
    session_id: int,
    query_text: str,
    policy: dict[str, Any],
    fetch_batch: FetchBatch,
    enroll_candidate: EnrollCandidate = materialize_online_candidate,
    candidate_budget: int = ONLINE_CANDIDATE_BUDGET,
    max_provider_rounds: int = ONLINE_MAX_PROVIDER_ROUNDS,
    round_gate: Callable[[int], dict[str, Any]] | None = None,
    exhaustion_reason: str = "bounded_provider_batch_exhausted",
    search_brief: dict[str, Any] | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Session-safe entry point: local dedupe keys always come from the DB."""
    from app.db.connection import get_conn
    conn = get_conn()
    local_snapshot = profile_online_inventory.local_identity_snapshot_for_session(int(session_id), conn=conn)
    inventory_snapshot = profile_online_inventory.inventory_alias_snapshot(conn=conn)
    return await collect_strict_online_candidates(
        query_text=query_text,
        policy=policy,
        local_canonical_keys=set(local_snapshot["aliases"]),
        inventory_aliases=set(inventory_snapshot["aliases"]),
        local_unique_count=int(local_snapshot["unique_count"]),
        inventory_snapshot_rows=int(inventory_snapshot["row_count"]),
        inventory_db_reads=int(inventory_snapshot["db_reads"]),
        fetch_batch=fetch_batch,
        enroll_candidate=enroll_candidate,
        candidate_budget=candidate_budget,
        max_provider_rounds=max_provider_rounds,
        round_gate=round_gate,
        exhaustion_reason=exhaustion_reason,
        search_brief=search_brief,
        as_of=as_of,
    )
