"""Decision, effect, and response seams for provider-backed KOL discovery.

The public provider module owns compatibility bindings and passes its live
dependencies into this module.  Keeping dependencies explicit is important:
legacy callers and tests monkeypatch the provider facade, so leaf helpers must
not capture alternate copies of those call sites at import time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DiscoveryPlan:
    query: str
    search_term: str
    relevance_language: str
    resolved_platforms: list[str]
    market: str
    safe_limit: int
    safe_per_platform: int
    leg_limits: dict[str, int]
    leg_cursors: dict[str, Any]
    auto_enroll: bool
    exclude_chinese: bool
    exact_query: bool
    pos_terms: list[str] = field(default_factory=list)
    neg_terms: list[str] = field(default_factory=list)


@dataclass
class DiscoveryState:
    survivors: list[dict[str, Any]] = field(default_factory=list)
    existing_matches: list[dict[str, Any]] = field(default_factory=list)
    platform_results: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    seen_keys: set[str] = field(default_factory=set)
    seen_aliases: set[str] = field(default_factory=set)
    gate_dropped: dict[str, int] = field(default_factory=lambda: {
        "hard_avoid": 0,
        "no_camera_signal": 0,
        "low_reach": 0,
        "brand_official": 0,
        "brand_official_lexicon": 0,
        "brand_official_dynamic": 0,
        "bio_irrelevant": 0,
        "persona_avoid_penalized": 0,
    })


@dataclass(frozen=True)
class CandidateDecision:
    disposition: str
    gate: str = ""
    gate_detail: str = ""
    relevance: dict[str, Any] | None = None


@dataclass(frozen=True)
class DiscoveryEffects:
    auto_enrolled_count: int
    analyzing_total: int


def prepare_discovery_plan(
    *,
    query_text: str,
    platforms: Any,
    platform_hint: str,
    market: str,
    limit: int,
    per_platform_limit: int,
    per_platform_limits: Any,
    search_query_en: str,
    product_focus: Any,
    ideal_creator_types: Any,
    verticals: Any,
    auto_enroll: bool,
    exclude_chinese: bool,
    page_cursors: Any,
    exact_query: bool,
    text_value: Any,
    int_value: Any,
    market_to_language: Any,
    localize_search_terms: Any,
    has_cjk: Any,
    persona_positive_terms: Any,
    strict_platforms: Any,
    sanitize_limits: Any,
    resolve_limit: Any,
    normalize_leg_cursors: Any,
) -> DiscoveryPlan:
    """Normalize request values without starting provider or persistence effects."""
    query = text_value(search_query_en) or text_value(query_text)
    relevance_language, _region_code = market_to_language(market)
    search_term = localize_search_terms(query, relevance_language)
    if relevance_language == "en" and has_cjk(search_term):
        fallback_terms = [
            term
            for term in persona_positive_terms(
                product_focus, ideal_creator_types, verticals, ""
            )
            if not has_cjk(term)
        ]
        if fallback_terms:
            search_term = " ".join(fallback_terms[:8])
    safe_limit_cap = 150 if not auto_enroll else 50
    safe_limit = max(1, min(int_value(limit, 15), safe_limit_cap))
    safe_per_platform = max(1, min(int_value(per_platform_limit, 15), 50))
    platform_limits = sanitize_limits(per_platform_limits)
    resolved_platforms = strict_platforms(platforms, fallback=platform_hint)
    leg_limits = {
        platform: resolve_limit(platform, safe_per_platform, platform_limits)
        for platform in resolved_platforms
    }
    return DiscoveryPlan(
        query=query,
        search_term=search_term,
        relevance_language=relevance_language,
        resolved_platforms=resolved_platforms,
        market=text_value(market).upper(),
        safe_limit=safe_limit,
        safe_per_platform=safe_per_platform,
        leg_limits=leg_limits,
        leg_cursors=normalize_leg_cursors(page_cursors),
        auto_enroll=bool(auto_enroll),
        exclude_chinese=bool(exclude_chinese),
        exact_query=bool(exact_query),
    )


def invalid_plan_response(plan: DiscoveryPlan) -> dict[str, Any] | None:
    """Project the two fail-fast responses; valid plans return ``None``."""
    if not plan.query:
        return {
            "status": "invalid_query",
            "query": plan.query,
            "platforms": plan.resolved_platforms,
            "items": [],
            "new_creators": [],
            "existing_matches": [],
            "provider_calls": False,
            "message": "query is required",
        }
    if not plan.resolved_platforms:
        return {
            "status": "invalid_platform",
            "query": plan.query,
            "platforms": [],
            "items": [],
            "new_creators": [],
            "existing_matches": [],
            "provider_calls": False,
            "message": "no supported discovery platform was selected",
        }
    return None


def _platform_items(
    raw_items: list[Any],
    *,
    platform: str,
    platform_signals: Any,
) -> tuple[list[dict[str, Any]], int]:
    strict_items: list[dict[str, Any]] = []
    mismatch_count = 0
    for raw in raw_items:
        item = dict(raw or {})
        signals = platform_signals(item)
        if signals and signals != {platform}:
            mismatch_count += 1
            continue
        item["platform"] = platform
        strict_items.append(item)
    return strict_items, mismatch_count


async def search_provider_legs(
    plan: DiscoveryPlan,
    *,
    enrich_prefilter: Any,
    search_platform: Any,
    annotate_platform_items: Any,
    canonicalize_candidates: Any,
    platform_signals: Any,
    run_legs: Any,
    deadline_seconds: Any,
    logger: Any,
) -> list[Any]:
    """Effect adapter for concurrent platform calls and provider normalization."""

    async def search_one(platform: str) -> dict[str, Any]:
        try:
            result = await search_platform(
                platform,
                plan.search_term,
                market=plan.market,
                max_results=plan.leg_limits.get(platform, plan.safe_per_platform),
                relevance_language=plan.relevance_language,
                strict_evidence=not plan.auto_enroll,
                enrich_prefilter=enrich_prefilter,
                deadline_seconds=deadline_seconds(platform),
                page_cursor=plan.leg_cursors.get(platform),
                exact_query=plan.exact_query,
            )
        except Exception:
            logger.warning(
                "profile discovery provider failed platform=%s",
                platform,
                exc_info=True,
            )
            return {
                "platform": platform,
                "status": "failed",
                "message": "platform_search_unavailable",
                "annotated": [],
                "error": True,
            }
        raw_items = list(result.get("items") or [])
        strict_items, mismatch_count = _platform_items(
            raw_items,
            platform=platform,
            platform_signals=platform_signals,
        )
        annotated = annotate_platform_items(strict_items, platform=platform)
        annotated = canonicalize_candidates(annotated, platform=platform)
        return {
            "platform": platform,
            "status": result.get("status"),
            "message": result.get("message"),
            "metadata": result.get("metadata") or {},
            "annotated": annotated,
            "filtered_platform_mismatch": mismatch_count,
            "error": result.get("status") not in {"done", "ready"} and not annotated,
        }

    return await run_legs(plan.resolved_platforms, search_one)


def _project_platform_outcome(
    platform: str,
    outcome: Any,
    *,
    requested_limit: int,
    account_leg: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
    if isinstance(outcome, BaseException):
        message = str(outcome)[:500]
        return (
            {
                "platform": platform,
                "status": "failed",
                "returned": 0,
                "metadata": {},
                "message": message,
            },
            [],
            {"platform": platform, "status": "failed", "message": message},
        )
    annotated = list(outcome.get("annotated") or [])
    row = {
        "platform": platform,
        "status": outcome.get("status"),
        "returned": len(annotated),
        "metadata": outcome.get("metadata") or {},
        "message": outcome.get("message"),
        "filtered_platform_mismatch": int(
            outcome.get("filtered_platform_mismatch") or 0
        ),
        **account_leg(outcome),
        "requested_limit": requested_limit,
    }
    error = None
    if outcome.get("error"):
        error = {
            "platform": platform,
            "status": outcome.get("status"),
            "message": outcome.get("message"),
        }
    return row, annotated, error


def _is_duplicate_candidate(
    item: dict[str, Any],
    *,
    platform: str,
    state: DiscoveryState,
    creator_aliases: Any,
    candidate_key: Any,
) -> bool:
    aliases = creator_aliases({**item, "platform": platform})
    fallback_key = candidate_key(item, platform)
    if aliases:
        if aliases.intersection(state.seen_aliases):
            return True
        state.seen_aliases.update(aliases)
    elif fallback_key in state.seen_keys:
        return True
    state.seen_keys.add(fallback_key)
    return False


def candidate_decision(
    item: dict[str, Any],
    *,
    plan: DiscoveryPlan,
    brand_gate: Any,
    detect_excluded_region: Any,
    is_garbage: Any,
    is_own_brand: Any,
    is_hard_avoid: Any,
    has_camera_signal: Any,
    is_bio_irrelevant: Any,
    reach_floor_reason: Any,
    persona_relevance: Any,
) -> CandidateDecision:
    """Pure candidate disposition; counters, logging, and writes stay outside."""
    official_reason = brand_gate(item)
    if official_reason:
        return CandidateDecision(
            "dropped", gate="brand_official", gate_detail=str(official_reason)
        )
    if item.get("historical_match") or item.get("history_kol_pool_id"):
        return CandidateDecision("existing")
    region = detect_excluded_region(item) if plan.exclude_chinese else ""
    if is_garbage(item) or region:
        return CandidateDecision("dropped")
    if is_own_brand(item):
        return CandidateDecision("dropped", gate="own_brand")
    if is_hard_avoid(item, plan.neg_terms):
        return CandidateDecision("dropped", gate="hard_avoid")
    if not has_camera_signal(item):
        return CandidateDecision("dropped", gate="no_camera_signal")
    if is_bio_irrelevant(item):
        return CandidateDecision("dropped", gate="bio_irrelevant")
    reach_reason = reach_floor_reason(item) if plan.auto_enroll else ""
    if reach_reason:
        return CandidateDecision(
            "dropped", gate="low_reach", gate_detail=str(reach_reason)
        )
    return CandidateDecision(
        "survivor",
        relevance=persona_relevance(
            item,
            pos_terms=plan.pos_terms,
            neg_terms=plan.neg_terms,
        ),
    )


def _record_candidate_decision(
    item: dict[str, Any],
    *,
    platform: str,
    decision: CandidateDecision,
    state: DiscoveryState,
    logger: Any,
) -> None:
    if decision.disposition == "existing":
        state.existing_matches.append(item)
        return
    if decision.disposition == "survivor":
        item.update(decision.relevance or {})
        if item.get("persona_avoid_hits"):
            state.gate_dropped["persona_avoid_penalized"] += 1
        state.survivors.append(item)
        return
    if not decision.gate:
        return
    state.gate_dropped[decision.gate] = state.gate_dropped.get(decision.gate, 0) + 1
    if decision.gate == "brand_official":
        subkey = f"brand_official_{decision.gate_detail}"
        state.gate_dropped[subkey] = state.gate_dropped.get(subkey, 0) + 1
        logger.debug(
            "discovery_brand_official_excluded handle=%r platform=%s via=%s",
            item.get("handle"),
            platform,
            decision.gate_detail,
        )
    elif decision.gate == "bio_irrelevant":
        logger.debug(
            "discovery_bio_irrelevant_excluded handle=%r platform=%s",
            item.get("handle"),
            platform,
        )
    elif decision.gate == "low_reach":
        logger.debug(
            "discovery_reach_floor_filtered handle=%r platform=%s reason=%s",
            item.get("handle"),
            platform,
            decision.gate_detail,
        )


def collect_discovery_state(
    plan: DiscoveryPlan,
    platform_outcomes: list[Any],
    *,
    account_leg: Any,
    creator_aliases: Any,
    candidate_key: Any,
    brand_gate: Any,
    detect_excluded_region: Any,
    is_garbage: Any,
    is_own_brand: Any,
    is_hard_avoid: Any,
    has_camera_signal: Any,
    is_bio_irrelevant: Any,
    reach_floor_reason: Any,
    persona_relevance: Any,
    logger: Any,
) -> DiscoveryState:
    """Merge deterministic platform outcomes through dedupe and pure gates."""
    state = DiscoveryState()
    for platform, outcome in zip(plan.resolved_platforms, platform_outcomes):
        row, annotated, error = _project_platform_outcome(
            platform,
            outcome,
            requested_limit=plan.leg_limits.get(platform, plan.safe_per_platform),
            account_leg=account_leg,
        )
        state.platform_results.append(row)
        if error is not None:
            state.errors.append(error)
        for item in annotated:
            if _is_duplicate_candidate(
                item,
                platform=platform,
                state=state,
                creator_aliases=creator_aliases,
                candidate_key=candidate_key,
            ):
                continue
            decision = candidate_decision(
                item,
                plan=plan,
                brand_gate=brand_gate,
                detect_excluded_region=detect_excluded_region,
                is_garbage=is_garbage,
                is_own_brand=is_own_brand,
                is_hard_avoid=is_hard_avoid,
                has_camera_signal=has_camera_signal,
                is_bio_irrelevant=is_bio_irrelevant,
                reach_floor_reason=reach_floor_reason,
                persona_relevance=persona_relevance,
            )
            _record_candidate_decision(
                item,
                platform=platform,
                decision=decision,
                state=state,
                logger=logger,
            )
    return state


def log_gate_summary(state: DiscoveryState, *, query: str, logger: Any) -> None:
    gates = state.gate_dropped
    total_dropped = (
        gates["hard_avoid"]
        + gates["no_camera_signal"]
        + gates["low_reach"]
        + gates["brand_official"]
        + gates["bio_irrelevant"]
    )
    if not total_dropped and not gates["persona_avoid_penalized"]:
        return
    logger.info(
        "camera_relevance_gate dropped=%d hard_avoid=%d no_camera_signal=%d low_reach=%d brand_official=%d bio_irrelevant=%d persona_avoid_penalized=%d survivors=%d query=%r",
        total_dropped,
        gates["hard_avoid"],
        gates["no_camera_signal"],
        gates["low_reach"],
        gates["brand_official"],
        gates["bio_irrelevant"],
        gates["persona_avoid_penalized"],
        len(state.survivors),
        query,
    )


def select_new_creators(
    survivors: list[dict[str, Any]],
    *,
    platforms: list[str],
    limit: int,
    text_value: Any,
    int_value: Any,
) -> list[dict[str, Any]]:
    """Rank candidates, then take a deterministic platform round-robin."""
    survivors.sort(
        key=lambda item: (
            float(item.get("relevance_score") or 0.0),
            int_value(item.get("avg_views") or item.get("views")),
            int_value(item.get("comments")),
            int_value(item.get("likes")),
        ),
        reverse=True,
    )
    by_platform: dict[str, list[dict[str, Any]]] = {}
    for item in survivors:
        by_platform.setdefault(text_value(item.get("platform")).lower(), []).append(item)
    order = [platform for platform in platforms if by_platform.get(platform)]
    for extra in by_platform:
        if extra not in order:
            order.append(extra)
    cursors = {platform: 0 for platform in order}
    selected: list[dict[str, Any]] = []
    while len(selected) < limit and order:
        progressed = False
        for platform in order:
            if len(selected) >= limit:
                break
            rows = by_platform.get(platform) or []
            if cursors[platform] < len(rows):
                selected.append(rows[cursors[platform]])
                cursors[platform] += 1
                progressed = True
        if not progressed:
            break
    return selected


def _annotate_reach(items: list[dict[str, Any]], *, reach_state: Any) -> int:
    analyzing = 0
    for item in items:
        if reach_state(item) == "unknown":
            item["reach_status"] = "analyzing"
            analyzing += 1
        else:
            item["reach_status"] = "ok"
    return analyzing


def apply_discovery_effects(
    plan: DiscoveryPlan,
    state: DiscoveryState,
    new_creators: list[dict[str, Any]],
    *,
    reach_state: Any,
    triage_existing: Any,
    auto_enroll_discoveries: Any,
    warm_avatar_cache: Any,
    logger: Any,
) -> DiscoveryEffects:
    """Run reach triage, persistence, then cache warming in legacy order."""
    analyzing_new = _annotate_reach(new_creators, reach_state=reach_state)
    existing_reach = {"low_reach": 0, "analyzing": 0}
    if plan.auto_enroll:
        state.existing_matches, existing_reach = triage_existing(state.existing_matches)
    state.gate_dropped["low_reach"] += existing_reach["low_reach"]
    analyzing_total = analyzing_new + existing_reach["analyzing"]

    auto_enrolled_count = 0
    if plan.auto_enroll:
        try:
            auto_enrolled_count = auto_enroll_discoveries(new_creators)
        except Exception as exc:
            logger.info("auto_enroll_discovery batch skipped: %s", str(exc)[:200])
        try:
            warm_avatar_cache(new_creators)
        except Exception:
            logger.debug("discovery avatar warmup call skipped", exc_info=True)
    return DiscoveryEffects(
        auto_enrolled_count=auto_enrolled_count,
        analyzing_total=analyzing_total,
    )


def _response_status(state: DiscoveryState, new_creators: list[dict[str, Any]]) -> str:
    has_items = bool(new_creators or state.existing_matches)
    if state.errors and has_items:
        return "partial"
    if state.errors:
        return "failed"
    return "ready" if has_items else "empty"


def project_discovery_response(
    plan: DiscoveryPlan,
    state: DiscoveryState,
    new_creators: list[dict[str, Any]],
    effects: DiscoveryEffects,
    *,
    enroll_skips: dict[str, int],
    brand_official_skip_reason: str,
    pagination: dict[str, Any],
    build_funnel: Any,
) -> dict[str, Any]:
    """Pure response projection from collected facts and completed effects."""
    return {
        "status": _response_status(state, new_creators),
        "query": plan.query,
        "platforms": plan.resolved_platforms,
        "market": plan.market,
        "query_mode": "exact_query_cell" if plan.exact_query else "expanded_ladder",
        "limit": plan.safe_limit,
        "per_platform_limit": plan.safe_per_platform,
        "per_platform_limits": plan.leg_limits,
        "items": [*state.existing_matches, *new_creators],
        "new_creators": new_creators,
        "existing_matches": state.existing_matches,
        "counts": {
            "new_creators": len(new_creators),
            "existing_matches": len(state.existing_matches),
            "auto_enrolled": effects.auto_enrolled_count,
            "enroll_skipped_brand_official": int(
                enroll_skips.get(brand_official_skip_reason) or 0
            ),
            "enroll_skipped_by_reason": enroll_skips,
            "filtered_low_reach": state.gate_dropped["low_reach"],
            "excluded_brand_official": state.gate_dropped["brand_official"],
            "excluded_brand_official_lexicon": state.gate_dropped[
                "brand_official_lexicon"
            ],
            "excluded_brand_official_dynamic": state.gate_dropped[
                "brand_official_dynamic"
            ],
            "excluded_bio_irrelevant": state.gate_dropped["bio_irrelevant"],
            "persona_avoid_penalized": state.gate_dropped[
                "persona_avoid_penalized"
            ],
            "analyzing": effects.analyzing_total,
            "platforms": len(plan.resolved_platforms),
            "errors": len(state.errors),
            "deadline_exceeded_platforms": sum(
                1
                for row in state.platform_results
                if row.get("deadline_exceeded")
            ),
        },
        "platform_results": state.platform_results,
        "next_page_cursors": pagination["next_page_cursors"],
        "next_cursor": pagination["next_cursor"],
        "has_more": pagination["has_more"],
        "discovery_funnel": build_funnel(
            platform_results=state.platform_results,
            gate_dropped=state.gate_dropped,
            survivors=len(state.survivors),
            returned_new_creators=len(new_creators),
            existing_matched=len(state.existing_matches),
        ),
        "errors": state.errors,
        "provider_calls": True,
    }
