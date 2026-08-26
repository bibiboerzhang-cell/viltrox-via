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
    profile_online_facets,
    profile_online_identity,
    profile_online_inventory,
    profile_recall_qualification,
)
from app.domains.kol.profile_recall_match_evidence import (
    build_match_evidence,
    candidate_facets,
)
from app.domains.kol.search_sessions_serde import project_public_asset_url, project_public_profile_text


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
    policy.update({
        "origin_lane": ONLINE_ORIGIN_LANE,
        "online_schema": ONLINE_SCHEMA,
        "online_policy_version": ONLINE_POLICY_VERSION,
        "target_count": ONLINE_TARGET,
        "require_trusted_market": bool(policy.get("market")),
        "supported_platforms": sorted(ONLINE_SUPPORTED_PLATFORMS),
        "exclude_chinese_regions": bool(exclude_chinese),
        "evidence_sources": {
            "followers": "online_provider.followers",
            "language": "online_provider.language",
            "profile_type": "online_provider.profile_type",
            "platform": "online_provider.platform",
        },
    })
    return policy


def _text(value: Any) -> str:
    return str(value or "").strip()


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _profile_url(raw: dict[str, Any]) -> str:
    return _text(profile_online_identity.stable_creator_identity(raw).get("profile_url"))


def _looks_like_video_url(value: Any) -> bool:
    for platform in ("youtube", "instagram", "tiktok"):
        if profile_online_identity.is_platform_video_url(value, platform=platform):
            return True
    return False


def _latest_video_evidence(raw: dict[str, Any]) -> dict[str, Any]:
    return profile_online_identity.latest_video_evidence(raw)


def _candidate_row(raw: dict[str, Any]) -> dict[str, Any]:
    identity = profile_online_identity.stable_creator_identity(raw)
    platform = _text(identity.get("platform"))
    handle = _text(identity.get("handle"))
    display_name = project_public_profile_text(
        raw.get("display_name") or raw.get("channel_name") or raw.get("name"),
        limit=240,
    )
    country = raw.get("country")
    country_source = _text(raw.get("country_source") or raw.get("market_source")).lower()
    # An online provider's bare country label is not equivalent to the pool's
    # legacy declared-country column.  Mark its missing provenance untrusted.
    if country and not country_source:
        country_source = "online_provider_unverified"
    language_evidence = profile_online_facets.adapt_language(raw)
    profile_type_evidence = profile_online_facets.adapt_profile_type(raw)
    return {
        "platform": platform,
        "handle": handle,
        "display_name": display_name,
        "profile_url": identity.get("profile_url"),
        "avatar_url": project_public_asset_url(raw.get("avatar_url") or raw.get("avatar")),
        "followers": raw.get("followers") or raw.get("subscriber_count") or raw.get("follower_count"),
        "country": country,
        "country_source": country_source,
        "language": language_evidence.get("value"),
        "language_source": language_evidence.get("source"),
        "profile_type": profile_type_evidence.get("value"),
        "profile_type_source": profile_type_evidence.get("source"),
        "facet_evidence": {
            "language": language_evidence,
            "profile_type": profile_type_evidence,
        },
        "bio": _text(raw.get("bio") or raw.get("description"))[:1000],
        "primary_topic": _text(raw.get("primary_topic"))[:300],
        "content_style": _text(raw.get("content_style"))[:300],
        "secondary_topics_json": raw.get("secondary_topics_json") or [],
        "profile_text": _text(raw.get("profile_text"))[:1000],
        "type_reason": _text(raw.get("type_reason"))[:300],
        # Never feed arbitrary provider blobs into market qualification.
        "raw_platform_data": "{}",
        "identity_projection_passed": identity.get("passed") is True,
    }


def _adapt_candidates(
    candidates: list[dict[str, Any]],
    *,
    query_text: str,
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]], dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    adapted: list[dict[str, Any]] = []
    rows_by_id: dict[int, dict[str, Any]] = {}
    evidence_by_id: dict[int, dict[str, Any]] = {}
    source_by_id: dict[int, dict[str, Any]] = {}
    for index, raw in enumerate(candidates):
        synthetic_id = 2_000_000_000 + index
        row = _candidate_row(raw)
        native_ids = profile_online_identity.safe_native_identity(raw, platform=row.get("platform"))
        latest = _latest_video_evidence(raw)
        representative = [{"title": latest.get("title")}] if latest.get("title") else []
        evidence = {
            "latest_real_video": latest,
            "representative_evidence": representative,
        }
        match_evidence = build_match_evidence(row, evidence, query_text)
        profile_type = _text(row.get("profile_type")).lower()
        item = {
            "kol_pool_id": synthetic_id,
            "platform": row.get("platform"),
            "handle": row.get("handle"),
            "display_name": row.get("display_name"),
            "profile_url": row.get("profile_url"),
            "avatar_url": row.get("avatar_url"),
            "followers": row.get("followers"),
            "language": row.get("language"),
            "profile_type": row.get("profile_type"),
            "country": row.get("country"),
            **native_ids,
            "facet_evidence": row.get("facet_evidence"),
            "bucket": "reviewer" if profile_type == "reviewer" else "creator",
            "match_evidence": match_evidence,
            "candidate_facets": candidate_facets(row, evidence),
            "display_rank_score": raw.get("display_rank_score") or raw.get("relevance_score") or raw.get("score"),
            "recall_rank_score": raw.get("recall_rank_score") or raw.get("relevance_score") or raw.get("score"),
        }
        adapted.append(item)
        rows_by_id[synthetic_id] = row
        evidence_by_id[synthetic_id] = evidence
        source_by_id[synthetic_id] = raw
    return adapted, rows_by_id, evidence_by_id, source_by_id


def _identity_probe(raw: dict[str, Any]) -> dict[str, Any]:
    row = _candidate_row(raw)
    return {
        **row,
        **profile_online_identity.safe_native_identity(raw, platform=row.get("platform")),
    }


def _project_online_item(item: dict[str, Any]) -> dict[str, Any]:
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
    proof = dict(projected.get("qualification_evidence") or {})
    proof.pop("kol_pool_id", None)
    projected["qualification_evidence"] = proof
    return projected


def _qualify_online_candidates_internal(
    candidates: list[dict[str, Any]],
    *,
    query_text: str,
    policy: dict[str, Any],
    local_canonical_keys: set[str],
    remaining: int,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    adapted, rows, evidence, sources = _adapt_candidates(candidates, query_text=query_text)
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
    selected_ids = {int(item.get("kol_pool_id") or 0) for item in selected}
    outcomes: list[dict[str, Any]] = []
    for item in adapted:
        synthetic_id = int(item.get("kol_pool_id") or 0)
        proof = item.get("qualification_evidence") if isinstance(item.get("qualification_evidence"), dict) else {}
        reasons = list(proof.get("rejection_reasons") or [])
        eight_gates_passed = all(
            isinstance(proof.get(field), dict) and proof[field].get("passed") is True
            for field in (
                "account_quality", "followers", "activity", "market",
                "language", "profile_type", "platform", "relevance",
            )
        )
        if synthetic_id in selected_ids:
            status = "selected"
        elif "duplicate_local_identity" in reasons:
            status = "duplicate_local"
        elif "duplicate_canonical_identity" in reasons:
            status = "duplicate_online"
        elif proof.get("passed") is True:
            status = "qualified_overflow"
        elif reasons and set(reasons).issubset(_PENDING_REASONS):
            status = "pending"
        else:
            status = "rejected"
        outcomes.append({
            "status": status,
            "eight_gates_passed": eight_gates_passed,
            "reasons": reasons,
            "canonical_key": profile_recall_qualification.canonical_creator_key(item),
            "item": _project_online_item(item) if status in {"selected", "qualified_overflow"} else None,
            "source": sources.get(synthetic_id) if status in {"selected", "qualified_overflow"} else None,
        })
    return {"outcomes": outcomes, "strict_contract": strict_contract}


def qualify_online_candidates(
    candidates: list[dict[str, Any]],
    *,
    query_text: str,
    policy: dict[str, Any],
    local_canonical_keys: set[str] | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Provider-free strict qualification helper used by tests and orchestration."""
    result = _qualify_online_candidates_internal(
        [dict(item) for item in candidates if isinstance(item, dict)],
        query_text=query_text,
        policy=policy,
        local_canonical_keys=set(local_canonical_keys or set()),
        remaining=ONLINE_TARGET,
        as_of=as_of,
    )
    outcomes = result["outcomes"]
    accepted = [item["item"] for item in outcomes if item["status"] == "selected"]
    counts: dict[str, int] = {}
    for outcome in outcomes:
        counts[outcome["status"]] = counts.get(outcome["status"], 0) + 1
    return {
        "schema": ONLINE_SCHEMA,
        "origin_lane": ONLINE_ORIGIN_LANE,
        "accepted": accepted,
        "counts": counts,
        "rejected_by_reason": dict(result["strict_contract"].get("rejected_by_reason") or {}),
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
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Collect 30 pool-backed, cross-lane-unique strict candidates or shortfall.

    车道 2·``round_gate``:第 2 轮起、发 provider 之前的准入闸(时间/钱/是否还有下一页)。
    被闸拦下与「真的翻完了」必须分得开 —— 拦下时 ``has_more`` 原样保留(于是
    ``exhausted`` 仍为 False),终止原因用闸给的机器码,绝不冒充「已耗尽」。
    """
    started = perf_counter()
    budget = max(ONLINE_TARGET, min(int(candidate_budget or ONLINE_CANDIDATE_BUDGET), 500))
    max_rounds = max(1, min(int(max_provider_rounds or 1), 10))
    accepted: list[dict[str, Any]] = []
    accepted_aliases: set[str] = set()
    inventory_alias_set = set(inventory_aliases or set())
    local_rank_base = (
        max(0, min(int(local_unique_count), ONLINE_TARGET))
        if local_unique_count is not None
        else min(ONLINE_TARGET, len(local_canonical_keys))
    )
    rejected_by_reason: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    provider_rounds = 0
    provider_calls = 0
    evaluated = 0
    budget_used = 0
    materialization_db_reads = 0
    cursor: Any = None
    provider_failed = False
    has_more = True
    seen_batch_fingerprints: set[str] = set()
    gate_verdicts: list[dict[str, Any]] = []
    gate_stop_reason = ""

    while len(accepted) < ONLINE_TARGET and has_more and provider_rounds < max_rounds and budget_used < budget:
        if provider_rounds >= 1 and round_gate is not None:
            verdict = round_gate(provider_rounds + 1)
            verdict = verdict if isinstance(verdict, dict) else {}
            gate_verdicts.append(verdict)
            if verdict.get("allowed") is not True:
                # 拦下 ≠ 翻完了:has_more 保持上一轮的真值,exhausted 因此仍诚实为 False。
                gate_stop_reason = _text(verdict.get("reason")) or "round_gate_denied"
                break
        provider_rounds += 1
        request_limit = min(150, budget - budget_used)
        try:
            provider_result = await _maybe_await(fetch_batch(
                round_no=provider_rounds,
                limit=request_limit,
                cursor=cursor,
            ))
        except Exception:
            provider_failed = True
            status_counts["provider_failed"] = status_counts.get("provider_failed", 0) + 1
            break
        provider_result = provider_result if isinstance(provider_result, dict) else {}
        if provider_result.get("provider_calls") is not False:
            provider_calls += 1
        batch = _provider_candidates(provider_result)[:request_limit]
        budget_used += len(batch)
        evaluated += len(batch)
        fingerprint = hashlib.sha256(json.dumps([
            profile_recall_qualification.canonical_creator_key(item) for item in batch
        ], sort_keys=True).encode("utf-8")).hexdigest()
        if fingerprint in seen_batch_fingerprints:
            status_counts["duplicate_batch"] = status_counts.get("duplicate_batch", 0) + len(batch)
            has_more = False
            break
        seen_batch_fingerprints.add(fingerprint)

        fresh_batch: list[dict[str, Any]] = []
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
                fresh_batch.append(raw)
        qualified = _qualify_online_candidates_internal(
            fresh_batch,
            query_text=query_text,
            policy=policy,
            local_canonical_keys=local_canonical_keys,
            remaining=max(1, ONLINE_TARGET - len(accepted)),
            as_of=as_of,
        )
        for reason, count in (qualified["strict_contract"].get("rejected_by_reason") or {}).items():
            rejected_by_reason[str(reason)] = rejected_by_reason.get(str(reason), 0) + int(count or 0)
        for outcome in qualified["outcomes"]:
            outcome_status = str(outcome["status"])
            if outcome_status not in {"selected", "qualified_overflow"}:
                status_counts[outcome_status] = status_counts.get(outcome_status, 0) + 1
                continue
            if len(accepted) >= ONLINE_TARGET:
                status_counts["qualified_overflow"] = status_counts.get("qualified_overflow", 0) + 1
                continue
            source = outcome.get("source") if isinstance(outcome.get("source"), dict) else {}
            try:
                materialized = await _maybe_await(enroll_candidate(source))
            except Exception:
                status_counts["rejected"] = status_counts.get("rejected", 0) + 1
                rejected_by_reason["enrollment_failed"] = rejected_by_reason.get("enrollment_failed", 0) + 1
                continue
            if isinstance(materialized, dict):
                materialization_db_reads += max(0, int(materialized.get("db_reads") or 0))
            if isinstance(materialized, dict) and (
                materialized.get("duplicate_local_inventory") is True
                or materialized.get("matched_existing") is True
            ):
                status_counts["duplicate_local_inventory"] = status_counts.get("duplicate_local_inventory", 0) + 1
                continue
            kol_pool_id = _enrolled_pool_id(materialized)
            if not kol_pool_id:
                status_counts["rejected"] = status_counts.get("rejected", 0) + 1
                rejected_by_reason["enrollment_failed"] = rejected_by_reason.get("enrollment_failed", 0) + 1
                continue
            item = dict(outcome["item"] or {})
            proof = dict(item.get("qualification_evidence") or {})
            identity_fingerprint = _text(item.get("canonical_fingerprint"))
            if not re.fullmatch(r"[0-9a-f]{64}", identity_fingerprint):
                status_counts["rejected"] = status_counts.get("rejected", 0) + 1
                rejected_by_reason["identity_fingerprint_missing"] = (
                    rejected_by_reason.get("identity_fingerprint_missing", 0) + 1
                )
                continue
            proof.update({
                "kol_pool_id": kol_pool_id,
                "canonical_fingerprint": identity_fingerprint,
            })
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
                continue
            accepted_aliases.update(aliases)
            accepted.append(item)
            status_counts["accepted"] = status_counts.get("accepted", 0) + 1
            if len(accepted) >= ONLINE_TARGET:
                break

        cursor = provider_result.get("next_cursor")
        has_more = bool(provider_result.get("has_more") and cursor)
        if provider_result.get("status") == "failed" and not batch:
            provider_failed = True
            status_counts["provider_failed"] = status_counts.get("provider_failed", 0) + 1
            has_more = False

    shortfall = max(0, ONLINE_TARGET - len(accepted))
    shortfall_reasons = dict(rejected_by_reason)
    for reason in (
        "pending", "rejected", "duplicate_local", "duplicate_local_inventory",
        "duplicate_online", "duplicate_batch",
    ):
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

    core_counts = {
        "target_count": ONLINE_TARGET,
        "evaluated_count": evaluated,
        "returned_count": len(accepted),
        "shortfall": shortfall,
        "provider_rounds": provider_rounds,
        "candidate_budget_used": budget_used,
        "inventory_snapshot_rows": max(0, int(inventory_snapshot_rows or 0)),
        "inventory_db_reads": max(0, int(inventory_db_reads or 0)),
        "materialization_db_reads": materialization_db_reads,
    }
    snapshot_id = _snapshot_id(
        accepted,
        core_counts,
        query_text=query_text,
        policy=policy,
    )
    for item in accepted:
        item["snapshot_revision"] = provider_rounds
        item["snapshot_id"] = snapshot_id
        proof = dict(item.get("qualification_evidence") or {})
        proof.update({
            "snapshot_revision": provider_rounds,
            "snapshot_id": snapshot_id,
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
            "policy_version": ONLINE_POLICY_VERSION,
            "target_count": ONLINE_TARGET,
            "min_followers": policy.get("min_followers"),
            "max_video_age_days": policy.get("max_video_age_days"),
            "market": policy.get("market"),
            "platforms": list(policy.get("platforms") or []),
            "languages": list(policy.get("languages") or []),
            "profile_types": list(policy.get("profile_types") or []),
            "supported_platforms": sorted(ONLINE_SUPPORTED_PLATFORMS),
            "exclude_chinese_regions": policy.get("exclude_chinese_regions") is True,
        },
        "query": {
            "query_text": _text(query_text)[:500],
            "source": "server_effective_query",
        },
        "status": "ready" if not shortfall else "shortfall",
        "terminal": True,
        "snapshot_complete": True,
        "snapshot_revision": max(1, provider_rounds),
        "snapshot_id": snapshot_id,
        "target_count": ONLINE_TARGET,
        "evaluated_count": evaluated,
        "strict_qualified_count": (
            status_counts.get("accepted", 0)
            + status_counts.get("qualified_overflow", 0)
            + status_counts.get("duplicate_local", 0)
            + status_counts.get("duplicate_online", 0)
        ),
        "net_new_accepted_count": len(accepted),
        "returned_count": len(accepted),
        "pending_count": status_counts.get("pending", 0),
        "rejected_count": status_counts.get("rejected", 0),
        "qualified_overflow_count": status_counts.get("qualified_overflow", 0),
        "duplicate_local_count": status_counts.get("duplicate_local", 0),
        "duplicate_local_inventory_count": status_counts.get("duplicate_local_inventory", 0),
        "duplicate_online_count": status_counts.get("duplicate_online", 0) + status_counts.get("duplicate_batch", 0),
        "provider_rounds": provider_rounds,
        "provider_calls": provider_calls,
        "candidate_budget": budget,
        "candidate_budget_used": budget_used,
        "inventory_snapshot_rows": max(0, int(inventory_snapshot_rows or 0)),
        "inventory_db_reads": max(0, int(inventory_db_reads or 0)),
        "materialization_db_reads": materialization_db_reads,
        "total_identity_db_reads": max(0, int(inventory_db_reads or 0)) + materialization_db_reads,
        # 「真的没有下一页」才叫 exhausted。被轮次闸(时间/钱)拦下时 has_more 仍为 True,
        # 这里就诚实说没耗尽 —— 差多少人由 shortfall_reasons 里的闸原因交代。
        "exhausted": not has_more,
        "round_gate": {
            "stopped_by": gate_stop_reason or None,
            "verdicts": gate_verdicts,
        },
        "shortfall": shortfall,
        "shortfall_reasons": shortfall_reasons,
        "rejected_by_reason": rejected_by_reason,
        "stage_timing": {"online_qualification_ms": round((perf_counter() - started) * 1000.0, 3)},
        "items": accepted,
        "provider_calls_performed": provider_calls > 0,
        "viltrox_fit_score_untouched": True,
    }


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
        as_of=as_of,
    )
