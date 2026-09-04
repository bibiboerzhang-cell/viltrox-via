"""Online-discovery stage for the queued profile-discovery pipeline.

The strict multi-round lane and legacy lane share one evidence ledger, but keep
their provider and materialization contracts separate.  The stage receives the
facade's provider/annotation callables so existing test and runtime patch points
remain authoritative.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

DiscoverCallable = Callable[..., Awaitable[dict[str, Any]]]
AnnotateCallable = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class OnlineDependencies:
    profile_discovery_evidence: Any
    profile_discovery_rounds: Any
    profile_discovery_targeted_batch: Any
    profile_online_qualification: Any
    recall_favorite_exclusion: Any
    search_session_diagnostics: Any
    search_sessions: Any
    completion_contract: Callable[..., dict[str, Any]]
    int_value: Callable[..., int]
    text: Callable[[Any], str]
    load_persona: Callable[[str], dict[str, Any]]
    logger: Any


@dataclass(frozen=True)
class DiscoveryRequest:
    session_id: int
    query: str
    payload: dict[str, Any]
    operator_anchor: dict[str, Any]
    resolved_platforms: Any
    normalized_market: str
    followers_min: int | None
    followers_max: int | None
    follower_source: str
    follower_filter: dict[str, Any]
    query_cells: list[dict[str, Any]]
    query_cells_omitted: bool
    base_count: int
    advance_limit: int


@dataclass(frozen=True)
class DiscoveryOutcome:
    new_discovery: dict[str, Any] | None
    base_count: int


@dataclass
class _EvidenceLedger:
    provider_funnels: list[dict[str, Any]] = field(default_factory=list)
    term_rounds: list[dict[str, Any]] = field(default_factory=list)
    round_forecasts: list[dict[str, Any]] = field(default_factory=list)
    observed_candidates: list[dict[str, Any]] = field(default_factory=list)
    favorite_blocks: list[dict[str, Any]] = field(default_factory=list)
    round_legs: list[str] = field(default_factory=list)
    round_cursor: dict[str, Any] = field(default_factory=dict)
    round_yield: dict[str, int] = field(default_factory=lambda: {"last": 0})

    def targeted_state(self) -> dict[str, Any]:
        return {
            "round_forecasts": self.round_forecasts,
            "term_rounds": self.term_rounds,
            "observed_candidates": self.observed_candidates,
            "favorite_blocks": self.favorite_blocks,
            "round_legs": self.round_legs,
            "round_cursor": self.round_cursor,
            "round_yield": self.round_yield,
        }


def _discovery_kwargs(
    request: DiscoveryRequest,
    deps: OnlineDependencies,
) -> dict[str, Any]:
    operator_cells = [
        cell
        for cell in request.query_cells
        if isinstance(cell, dict)
        and bool(cell.get("segment_locked"))
        and deps.text(cell.get("segment_source")).startswith("operator_")
    ]
    persona = (
        {}
        if operator_cells
        else deps.load_persona(deps.text(request.payload.get("product_sku")))
    )
    operator_people_terms = [
        deps.text(cell.get("primary_query") or cell.get("segment_label") or cell.get("segment"))
        for cell in operator_cells
        if deps.text(cell.get("primary_query") or cell.get("segment_label") or cell.get("segment"))
    ]
    return {
        "query_text": request.query,
        "platforms": request.resolved_platforms,
        "platform_hint": deps.text(request.payload.get("platform")),
        "market": request.normalized_market,
        "exclude_chinese": bool(request.payload.get("exclude_chinese", True)),
        "search_query_en": request.query,
        "product_focus": operator_people_terms or request.payload.get("product_focus"),
        "ideal_creator_types": operator_people_terms or persona.get("ideal_creator_types_json"),
        "verticals": (
            [deps.text(cell.get("segment_label") or cell.get("segment")) for cell in operator_cells]
            if operator_cells else persona.get("verticals_json")
        ),
        "avoid_types": [] if operator_cells else persona.get("avoid_types_json"),
        "target_persona": deps.text(request.payload.get("target_persona")),
    }


class _StrictOnlineRunner:
    def __init__(
        self,
        *,
        request: DiscoveryRequest,
        discovery_kwargs: dict[str, Any],
        ledger: _EvidenceLedger,
        favorite_identity_keys: set[str],
        discover: DiscoverCallable,
        deps: OnlineDependencies,
    ) -> None:
        self.request = request
        self.discovery_kwargs = discovery_kwargs
        self.ledger = ledger
        self.favorite_identity_keys = favorite_identity_keys
        self.discover = discover
        self.deps = deps
        self.per_platform_limit = max(
            1,
            min(
                deps.int_value(request.payload.get("new_discovery_per_platform_limit"), 50),
                50,
            ),
        )
        self.per_platform_limits = request.payload.get("new_discovery_per_platform_limits")
        selected = request.resolved_platforms
        self.plan_legs = [
            deps.text(item)
            for item in (
                selected
                if isinstance(selected, (list, tuple, set))
                else [selected] if selected else []
            )
            if deps.text(item)
        ] or sorted(deps.profile_online_qualification.ONLINE_SUPPORTED_PLATFORMS)
        self.youtube_variants = deps.profile_discovery_evidence.planned_youtube_variants(
            request.query
        )

    async def fetch_batch(self, *, round_no: int, limit: int, cursor: Any) -> dict[str, Any]:
        if self.request.query_cells:
            return await self.deps.profile_discovery_targeted_batch.fetch_targeted_round(
                round_no=round_no,
                query_cells=self.request.query_cells,
                discovery_kwargs=self.discovery_kwargs,
                plan_legs=self.plan_legs,
                state=self.ledger.targeted_state(),
                favorite_identity_keys=self.favorite_identity_keys,
                discover=self.discover,
            )

        legs = self.deps.profile_discovery_rounds.platforms_for_round(
            round_no,
            self.ledger.round_legs,
            cursor,
        )
        if round_no > 1 and not legs:
            return {
                "status": "empty",
                "new_creators": [],
                "provider_calls": False,
                "has_more": False,
            }
        forecast = self.deps.profile_discovery_rounds.round_cost_forecast(
            legs if round_no > 1 else (self.ledger.round_legs or self.plan_legs),
            round_no=round_no,
            per_platform_limit=self.per_platform_limit,
            per_platform_limits=self.per_platform_limits,
            youtube_query_variants=self.youtube_variants,
        )
        self.ledger.round_forecasts.append(forecast)
        self.deps.logger.info(
            "discovery_round_forecast %s",
            self.deps.profile_discovery_rounds.forecast_line(forecast),
        )
        batch = await self.discover(
            **{
                **self.discovery_kwargs,
                **({"platforms": legs} if round_no > 1 else {}),
            },
            limit=max(1, min(limit, 150)),
            per_platform_limit=self.per_platform_limit,
            per_platform_limits=self.per_platform_limits,
            auto_enroll=False,
            page_cursors=cursor,
        )
        self.ledger.term_rounds.append(
            self.deps.profile_discovery_evidence.observe_round(
                round_no=round_no,
                platform_results=batch.get("platform_results"),
                candidates=batch.get("new_creators"),
            )
        )
        self.ledger.observed_candidates.extend(
            row for row in (batch.get("new_creators") or []) if isinstance(row, dict)
        )
        kept, favorite_block = (
            self.deps.recall_favorite_exclusion.exclude_favorited_online_candidates(
            batch.get("new_creators") or [],
            identity_keys=self.favorite_identity_keys,
            )
        )
        batch["new_creators"] = kept
        self.ledger.favorite_blocks.append(favorite_block)
        if not self.ledger.round_legs:
            self.ledger.round_legs.extend(
                self.deps.text(item)
                for item in (batch.get("platforms") or [])
                if self.deps.text(item)
            )
        self.ledger.round_cursor.clear()
        self.ledger.round_cursor.update(batch.get("next_cursor") or {})
        self.ledger.round_yield["last"] = len(batch.get("new_creators") or [])
        if isinstance(batch.get("discovery_funnel"), dict):
            self.ledger.provider_funnels.append(batch["discovery_funnel"])
        return batch

    async def run(self) -> tuple[dict[str, Any], dict[str, Any]]:
        round_gate = self.deps.profile_discovery_targeted_batch.build_pipeline_round_gate(
            query_cells=self.request.query_cells,
            discovery_kwargs=self.discovery_kwargs,
            plan_legs=self.plan_legs,
            state=self.ledger.targeted_state(),
            per_platform_limit=self.per_platform_limit,
            per_platform_limits=self.per_platform_limits,
        )
        online_result = await self.deps.profile_online_qualification.collect_strict_online_for_session(
            session_id=int(self.request.session_id),
            query_text=self.request.query,
            policy=self.deps.profile_online_qualification.online_policy(
                market=self.request.normalized_market,
                platforms=self.request.resolved_platforms,
                languages=(
                    self.request.payload.get("languages")
                    or self.request.payload.get("content_languages")
                ),
                profile_types=(
                    self.request.payload.get("profile_types")
                    or self.request.payload.get("kol_types")
                ),
                exclude_chinese=bool(self.request.payload.get("exclude_chinese", True)),
                followers_min=self.request.followers_min,
                followers_max=self.request.followers_max,
                source=self.request.follower_source,
                unknown_policy=(
                    self.deps.text(self.request.follower_filter.get("unknown_policy"))
                    or "pending"
                ),
            ),
            fetch_batch=self.fetch_batch,
            candidate_budget=150,
            max_provider_rounds=(
                self.deps.profile_online_qualification.ONLINE_MAX_PROVIDER_ROUNDS
            ),
            round_gate=round_gate,
            exhaustion_reason=self.deps.profile_discovery_targeted_batch.exhaustion_reason(
                self.request.query_cells
            ),
            search_brief=(
                self.request.payload.get("search_brief")
                if isinstance(self.request.payload.get("search_brief"), dict)
                else None
            ),
        )
        online_result = self.deps.profile_discovery_targeted_batch.finalize_online_result(
            online_result,
            query_cells=self.request.query_cells,
            query_cells_omitted=self.request.query_cells_omitted,
            search_brief=self.request.payload.get("search_brief"),
            objective=self.request.payload.get("objective"),
            state=self.ledger.targeted_state(),
        )
        online_result["enrichment_queue"] = {
            "status": "not_enriched",
            "async": False,
            "queued": 0,
            "already_queued": 0,
            "failed": 0,
        }
        online_result["_session_pipeline_running"] = True
        online_result["favorite_exclusion"] = (
            self.deps.recall_favorite_exclusion.merge_diagnostics(
            *self.ledger.favorite_blocks
            )
        )
        self.deps.search_sessions.attach_online_qualified_result(
            int(self.request.session_id),
            online_result,
        )
        online_contract = {
            key: value for key, value in online_result.items() if key != "items"
        }
        selected = self.request.resolved_platforms
        new_discovery = {
            "status": online_result.get("status"),
            "query": self.request.query,
            "platforms": (
                list(selected)
                if isinstance(selected, (list, tuple, set))
                else [selected] if selected else []
            ),
            "items": list(online_result.get("items") or []),
            "new_creators": list(online_result.get("items") or []),
            "existing_matches": [],
            "provider_calls": online_result.get("provider_calls_performed"),
            "online_qualification": online_contract,
            "favorite_exclusion": online_result.get("favorite_exclusion"),
        }
        return new_discovery, online_contract


async def _legacy_discovery(
    *,
    request: DiscoveryRequest,
    discovery_kwargs: dict[str, Any],
    ledger: _EvidenceLedger,
    favorite_identity_keys: set[str],
    discover: DiscoverCallable,
    annotate_priority: AnnotateCallable,
    deps: OnlineDependencies,
) -> dict[str, Any]:
    new_discovery = await discover(
        **discovery_kwargs,
        limit=max(
            1,
            min(deps.int_value(request.payload.get("new_discovery_limit"), 15), 50),
        ),
        per_platform_limit=max(
            1,
            min(
                deps.int_value(
                    request.payload.get("new_discovery_per_platform_limit"),
                    15,
                ),
                50,
            ),
        ),
        per_platform_limits=request.payload.get("new_discovery_per_platform_limits"),
    )
    ledger.term_rounds.append(
        deps.profile_discovery_evidence.observe_round(
            round_no=1,
            platform_results=new_discovery.get("platform_results"),
            candidates=new_discovery.get("new_creators"),
        )
    )
    ledger.observed_candidates.extend(
        row for row in (new_discovery.get("new_creators") or []) if isinstance(row, dict)
    )
    kept, favorite_block = deps.recall_favorite_exclusion.exclude_favorited_online_candidates(
        new_discovery.get("new_creators") or [],
        identity_keys=favorite_identity_keys,
    )
    new_discovery["new_creators"] = kept
    new_discovery["favorite_exclusion"] = favorite_block
    new_discovery = annotate_priority(new_discovery)
    if isinstance(new_discovery.get("discovery_funnel"), dict):
        ledger.provider_funnels.append(new_discovery["discovery_funnel"])
    return new_discovery


def _discovery_count(result: dict[str, Any]) -> int:
    count = len(result.get("existing_matches") or []) + len(result.get("new_creators") or [])
    return count if count > 0 else len(result.get("items") or [])


def _attach_legacy_progress(
    *,
    request: DiscoveryRequest,
    result: dict[str, Any],
    base_count: int,
    deps: OnlineDependencies,
) -> None:
    total = min(base_count, request.advance_limit)
    contract = deps.completion_contract(
        base_count=base_count,
        total=total,
        terminal_count=0,
        ready_count=0,
        active_tasks=total,
        requested_tasks_terminal=False,
    )
    deps.search_sessions.attach_new_discovery_result(
        int(request.session_id),
        {
            **result,
            "_session_pipeline_running": True,
            "_session_progress": {
                "base": base_count,
                "total": total,
                "profile_ready": 0,
                "profile_failed": 0,
                "complete_ready": 0,
                "complete_partial": 0,
                **contract,
            },
        },
    )


def _record_diagnostics(
    *,
    request: DiscoveryRequest,
    result: dict[str, Any],
    returned_count: int,
    strict_online: bool,
    online_contract: dict[str, Any] | None,
    ledger: _EvidenceLedger,
    deps: OnlineDependencies,
) -> None:
    lane = "online_strict" if strict_online else "legacy_discovery"
    strict_patch = (
        {
            "discovery_round_plan": deps.profile_discovery_rounds.round_plan_record(
                forecasts=ledger.round_forecasts,
                round_gate=(online_contract or {}).get("round_gate"),
                provider_rounds=(online_contract or {}).get("provider_rounds"),
                **deps.profile_discovery_targeted_batch.round_plan_actual_youtube_kwargs(
                    ledger.term_rounds
                ),
                actual_apify_runs=sum(
                    deps.int_value(row.get("apify_actor_runs"))
                    for row in ledger.term_rounds
                ),
            )
        }
        if strict_online
        else {}
    )
    deps.search_session_diagnostics.record_search_diagnostics(
        int(request.session_id),
        {
            deps.search_session_diagnostics.DISCOVERY_FUNNEL_KEY: (
                deps.search_session_diagnostics.build_discovery_funnel(
                    lane=lane,
                    provider_funnels=ledger.provider_funnels,
                    online_contract=online_contract,
                    discovery_counts=result.get("counts"),
                    returned_count=returned_count,
                )
            ),
            **strict_patch,
            deps.profile_discovery_evidence.TERM_EVIDENCE_KEY: (
                deps.profile_discovery_evidence.build_term_evidence(
                    lane=lane,
                    anchor=deps.profile_discovery_evidence.product_anchor_record(
                        payload=request.payload,
                        operator_anchor=request.operator_anchor,
                        effective_query=request.query,
                    ),
                    rounds=ledger.term_rounds,
                    observed_candidates=ledger.observed_candidates,
                    accepted_items=result.get("new_creators"),
                    **deps.profile_discovery_targeted_batch.term_evidence_youtube_forecast_kwargs(
                        ledger.round_forecasts
                    ),
                )
            ),
        },
    )


async def run_discovery(
    request: DiscoveryRequest,
    *,
    discover: DiscoverCallable,
    annotate_priority: AnnotateCallable,
    deps: OnlineDependencies,
) -> DiscoveryOutcome:
    if not bool(request.payload.get("include_new_discovery", True)):
        return DiscoveryOutcome(new_discovery=None, base_count=request.base_count)

    discovery_kwargs = _discovery_kwargs(request, deps)
    strict_online = request.payload.get("_smart_online_30_contract") is True
    ledger = _EvidenceLedger()
    favorite_identity_keys = deps.recall_favorite_exclusion.favorited_identity_keys()
    online_contract: dict[str, Any] | None = None
    if strict_online:
        runner = _StrictOnlineRunner(
            request=request,
            discovery_kwargs=discovery_kwargs,
            ledger=ledger,
            favorite_identity_keys=favorite_identity_keys,
            discover=discover,
            deps=deps,
        )
        new_discovery, online_contract = await runner.run()
    else:
        new_discovery = await _legacy_discovery(
            request=request,
            discovery_kwargs=discovery_kwargs,
            ledger=ledger,
            favorite_identity_keys=favorite_identity_keys,
            discover=discover,
            annotate_priority=annotate_priority,
            deps=deps,
        )

    returned_count = _discovery_count(new_discovery)
    base_count = request.base_count + returned_count
    if not strict_online:
        _attach_legacy_progress(
            request=request,
            result=new_discovery,
            base_count=base_count,
            deps=deps,
        )
    _record_diagnostics(
        request=request,
        result=new_discovery,
        returned_count=returned_count,
        strict_online=strict_online,
        online_contract=online_contract,
        ledger=ledger,
        deps=deps,
    )
    return DiscoveryOutcome(new_discovery=new_discovery, base_count=base_count)
