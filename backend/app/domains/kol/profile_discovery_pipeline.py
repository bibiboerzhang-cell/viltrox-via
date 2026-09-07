"""Queued smart-search profile advance pipeline.

This facade intentionally retains the established monkeypatch/runtime binding
seams. Stage implementations live in focused leaf modules so orchestration
order stays visible without one function owning every policy branch.
"""
from __future__ import annotations

from typing import Any
from copy import deepcopy
import asyncio
from uuid import uuid4

from app.core.logging import get_logger
from app.domains.kol import (
    derived_job_actor,
    profile_discovery_evidence,
    profile_discovery_local_lane,
    profile_discovery_pipeline_online,
    profile_discovery_pipeline_stages,
    profile_discovery_rounds,
    profile_discovery_targeted_batch,
    profile_online_qualification,
    profile_recall,
    profile_recall_qualification,
    recall_favorite_exclusion,
    search_session_diagnostics,
    search_sessions,
    targeted_search_runtime,
)
from app.domains.kol.discovery_filters import _annotate_new_priority, _int, _text
from app.domains.kol.profile_discovery_candidates import (
    explicit_platforms_from_query,
    filter_recall_result_market,
    filter_recall_result_platforms,
    resolve_market_constraint,
)
from app.domains.kol.profile_discovery_provider import discover_new_creators
from app.domains.kol.profile_recall_match_evidence import query_evidence_terms
from app.domains.kol.profile_discovery_session import (
    _profile_advance_pipeline_status,
    advance_search_session_items,
)
from app.domains.kol.search_progress_contract import completion_contract
from app.domains.kol.search_mode import normalize_search_mode
from app.domains.kol.search_execution_fence import bind_pipeline_execution, execution_kwargs, require_applied
from app.domains.kol.provider_job_access import ProviderJobAccessError

logger = get_logger(__name__)


def _stage_dependencies() -> profile_discovery_pipeline_stages.StageDependencies:
    from app.domains.kol import smart_query_planner

    return profile_discovery_pipeline_stages.StageDependencies(
        profile_discovery_evidence=profile_discovery_evidence,
        profile_recall_qualification=profile_recall_qualification,
        search_sessions=search_sessions,
        targeted_search_runtime=targeted_search_runtime,
        smart_query_planner=smart_query_planner,
        explicit_platforms_from_query=explicit_platforms_from_query,
        resolve_market_constraint=resolve_market_constraint,
        query_evidence_terms=query_evidence_terms,
        completion_contract=completion_contract,
        int_value=_int,
        text=_text,
    )


def _load_product_persona(product_sku: str) -> dict[str, Any]:
    if not product_sku:
        return {}
    try:
        from app.domains.costs import product_persona

        return product_persona.get_product_persona(product_sku) or {}
    except Exception as exc:
        logger.warning(
            "smart search product persona unavailable | error_type=%s",
            type(exc).__name__,
        )
        return {}


def _online_dependencies() -> profile_discovery_pipeline_online.OnlineDependencies:
    return profile_discovery_pipeline_online.OnlineDependencies(
        profile_discovery_evidence=profile_discovery_evidence,
        profile_discovery_rounds=profile_discovery_rounds,
        profile_discovery_targeted_batch=profile_discovery_targeted_batch,
        profile_online_qualification=profile_online_qualification,
        recall_favorite_exclusion=recall_favorite_exclusion,
        search_session_diagnostics=search_session_diagnostics,
        search_sessions=search_sessions,
        completion_contract=completion_contract,
        int_value=_int,
        text=_text,
        load_persona=_load_product_persona,
        logger=logger,
    )


def _enqueue_content_fit(
    *,
    session_id: int,
    payload: dict[str, Any],
    provider_actor: dict[str, Any] | None,
    deps: profile_discovery_pipeline_stages.StageDependencies,
) -> dict[str, Any] | None:
    if not bool(payload.get("include_content_fit", True)):
        return None
    try:
        from app.domains.kol import content_fit_enqueue

        return profile_discovery_pipeline_stages.enqueue_content_fit(
            session_id=session_id,
            payload=payload,
            provider_actor=provider_actor,
            queue_module=content_fit_enqueue,
            deps=deps,
        )
    except Exception:
        return {"status": "error", "reason": "content_fit_enqueue_failed"}


def _enqueue_video_backfill(
    *,
    session_id: int,
    payload: dict[str, Any],
    staff: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """懒回填派生的是**付费**账号深抓,必须带上发起人才铸得出围栏。

    历史写法把 ``staff`` 写死成 None,派生链一路无身份,孙任务(代表作深析)入队
    即被授权检查拒。这里改成直通:身份来自祖父任务 payload,取不到就不派生。
    """
    if not bool(payload.get("include_lazy_video_backfill", True)):
        return None
    try:
        from app.domains.kol import video_backfill_enqueue

        return video_backfill_enqueue.enqueue_lazy_video_backfill_for_session(
            session_id=int(session_id),
            top_n=max(
                1,
                min(
                    _int(
                        payload.get("lazy_video_backfill_top_n"),
                        video_backfill_enqueue.DEFAULT_TOP_N,
                    ),
                    video_backfill_enqueue.MAX_TOP_N,
                ),
            ),
            staff=staff,
        )
    except Exception:
        return {"status": "error", "reason": "video_backfill_enqueue_failed"}


def _interrupt_lanes(session_id: int, execution_id: str, exc: BaseException) -> None:
    reason = "search_pipeline_cancelled" if isinstance(exc, asyncio.CancelledError) else "search_pipeline_failed"
    try:
        search_sessions.interrupt_search_lanes(session_id, execution_id=execution_id, reason=reason)
    except Exception as cleanup_error:
        # Cleanup is best effort when storage is unavailable; never replace the
        # original error/cancellation or continue into provider/advance work.
        logger.warning("search lane terminal receipt unconfirmed | session_id=%s error_type=%s",
                       session_id, type(cleanup_error).__name__)


@bind_pipeline_execution
async def execute_smart_search_profile_advance_pipeline(
    *,
    session_id: int,
    payload: dict[str, Any],
    provider_actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a queued text recall/new-discovery/profile-advance pipeline."""
    payload = deepcopy(payload)
    search_mode = normalize_search_mode(payload.get("search_mode"))
    if "search_mode" in payload:
        payload["search_mode"] = search_mode
    if search_mode == "fresh_network":
        flag = payload.get("include_new_discovery", True)
        discovery_requested = (flag.strip().lower() not in {"", "0", "false", "no", "off"}
                               if isinstance(flag, str) else bool(flag))
        if not discovery_requested:
            raise ValueError("fresh_network conflicts with include_new_discovery=false")
        payload["include_new_discovery"] = True
    elif search_mode == "saved":
        payload["include_new_discovery"] = False
    stage_deps = _stage_dependencies()
    planning = profile_discovery_pipeline_stages.prepare_plan(
        session_id=int(session_id),
        payload=payload,
        deps=stage_deps,
    )
    if planning.early_result is not None:
        return planning.early_result

    recall_setup = profile_discovery_pipeline_stages.prepare_recall(
        payload,
        planning,
        stage_deps,
    )
    independent = search_mode == "hybrid" and payload.get("search_mode") == "hybrid" and payload.get("include_new_discovery") is True

    def read_recall() -> dict[str, Any]:
        result = targeted_search_runtime.execute_local_search(
            context=deepcopy(recall_setup.context),
            recall_kwargs=deepcopy(recall_setup.recall_kwargs),
            recall=profile_recall.recall_kol_profiles,
        )
        result = filter_recall_result_platforms(result, recall_setup.recall_filters.get("platforms"))
        result = filter_recall_result_market(result, planning.operator_market)
        return profile_recall_qualification.project_smart_local_result(result)

    async def run_online(base_count: int, advance_limit: int):
        return await profile_discovery_pipeline_online.run_discovery(
            profile_discovery_pipeline_online.DiscoveryRequest(
                session_id=int(session_id), query=planning.query, payload=payload,
                operator_anchor=planning.operator_anchor, resolved_platforms=recall_setup.resolved_platforms,
                normalized_market=planning.operator_market, followers_min=recall_setup.followers_min,
                followers_max=recall_setup.followers_max, follower_source=recall_setup.follower_source,
                follower_filter=recall_setup.follower_filter, query_cells=recall_setup.query_cells,
                query_cells_omitted=recall_setup.query_cells_omitted,
                base_count=base_count, advance_limit=advance_limit,
            ),
            discover=discover_new_creators, annotate_priority=_annotate_new_priority,
            deps=_online_dependencies(),
        )

    if independent:
        execution_id = _text(payload.get("_search_execution_id")) or uuid4().hex
        smart_local = payload.get("_smart_local_30_contract") is True
        cap = profile_recall_qualification.SMART_LOCAL_TARGET if smart_local else 15
        advance_limit = max(1, min(_int(payload.get("advance_limit") or payload.get("profile_advance_limit"), cap), cap))
        for lane in ("local", "online"):
            search_sessions.update_search_lane(int(session_id), lane=lane, status="running", execution_id=execution_id, **execution_kwargs(payload))

        async def local_branch():
            local_result = await profile_discovery_local_lane.read_local_lane(read_recall)
            local_status = _text(local_result.get("status"))
            if local_status in {"failed", "timeout", "capacity_unavailable"}:
                search_sessions.update_search_lane(
                    int(session_id), lane="local", status=local_status,
                    reason=_text((local_result.get("diagnostics") or {}).get("reason")),
                    **execution_kwargs(payload),
                )
                return profile_discovery_pipeline_stages.RecallState(
                    result=local_result, session=None, base_count=0,
                    advance_limit=advance_limit, smart_local_30=smart_local,
                )
            return profile_discovery_pipeline_stages.attach_recall(
                session_id=int(session_id), payload=payload, recall_result=local_result,
                deps=stage_deps, lane_only=True,
            )

        async def online_branch():
            try:
                return await run_online(0, advance_limit)
            except (ValueError, PermissionError, ProviderJobAccessError):
                raise
            except Exception:
                search_sessions.update_search_lane(int(session_id), lane="online", status="failed",
                                                   reason="online_discovery_failed", **execution_kwargs(payload))
                return profile_discovery_pipeline_online.DiscoveryOutcome(
                    new_discovery={"status": "failed", "reason": "online_discovery_failed", "items": []},
                    base_count=0,
                )

        branches = [asyncio.create_task(local_branch()), asyncio.create_task(online_branch())]
        try:
            recall, discovery = await asyncio.gather(*branches)
        except BaseException as exc:
            for branch_task in branches:
                branch_task.cancel()
            try:
                await asyncio.gather(*branches, return_exceptions=True)
            finally:
                _interrupt_lanes(int(session_id), execution_id, exc)
            raise
        recall_result = recall.result
        discovery = profile_discovery_pipeline_online.DiscoveryOutcome(
            new_discovery=discovery.new_discovery, base_count=recall.base_count + discovery.base_count,
        )
    # Keep this call in the facade: it is the provider-free recall seam used by
    # the runtime compatibility binder and the stage-order contract tests.
    elif search_mode == "fresh_network":
        recall_result = {
            "method": "fresh_network", "status": "not_requested", "items": [],
            "buckets": {"creator": [], "reviewer": []},
            "diagnostics": {"returned_count": 0, "local_recall_status": "skipped_by_explicit_mode",
                            "search_mode": "fresh_network", "inventory_recall_performed": False},
        }
    else:
        recall_result = read_recall()
    if not independent:
        recall = profile_discovery_pipeline_stages.attach_recall(
            session_id=int(session_id), payload=payload, recall_result=recall_result, deps=stage_deps,
        )

    if search_mode == "saved":
        # No profile advance, model analysis, lazy backfill or field-topup task
        # may be derived from a saved-only request, even with contradictory flags.
        session = search_sessions.update_session_result_summary(
            int(session_id), status="ready", summary_patch={
                "phase": "complete", "search_mode": "saved",
                "smart_search_profile_advance_job": {
                    "status": "ready", "query_text": planning.query,
                    "new_discovery_status": "not_requested", "advance_status": "not_requested",
                    "provider_calls_allowed": False,
                },
            },
            **execution_kwargs(payload),
        )
        require_applied(session)
        return {
            "status": "ready", "search_mode": "saved", "session_id": int(session_id),
            "query": planning.query, "query_plan_source": payload.get("query_plan_source"),
            "recall": recall_result, "new_discovery": None,
            "advance": {"status": "not_requested", "selected": 0, "counts": {}},
            "content_fit": None, "field_topup": None,
            "search_session": session or recall.session,
            "provider_calls_performed": False, "write_db": True,
            "writes": ["vkpi_kol_search_sessions", "vkpi_kol_search_session_items"],
            "viltrox_fit_score_changed_ids": [], "viltrox_fit_score_untouched": True,
        }

    if not independent:
        discovery = await run_online(recall.base_count, recall.advance_limit)

    advance_result = advance_search_session_items(
        session_id=int(session_id),
        smart_local_contract=recall.smart_local_30,
        body={
            **payload,
            "execute": True,
            "limit": recall.advance_limit,
            "_pipeline_running": True,
            "max_posts": max(1, min(_int(payload.get("max_posts"), 12), 12)),
            "mode": _text(
                payload.get("advance_mode") or payload.get("mode") or "account_deep"
            ),
            "item_types": payload.get("item_types")
            or ["new_creator", "existing_kol", "recall_candidate"],
            "include_completed": bool(payload.get("include_completed")),
        },
    )
    changed_ids = profile_discovery_pipeline_stages.changed_fit_ids(
        advance_result,
        stage_deps,
    )
    content_fit = _enqueue_content_fit(
        session_id=int(session_id),
        payload=payload,
        provider_actor=provider_actor,
        deps=stage_deps,
    )
    # 派生链的责任人:祖父任务 payload 里带的身份(worker 里没有请求上下文,而现建
    # 的会话 created_by 为 NULL,反查不出人)。解析一次,两个派生点共用。
    derived_staff = derived_job_actor.derived_job_staff(
        payload,
        provider_actor=provider_actor,
    )
    # Preserve the lazy-backfill side effect and ordering. Its receipt is not
    # part of the historical public return contract.
    _enqueue_video_backfill(
        session_id=int(session_id),
        payload=payload,
        staff=derived_staff,
    )

    field_topup: dict[str, Any] | None = None
    if bool(payload.get("include_field_topup", True)):
        try:
            from app.domains.kol import profile_field_topup_enqueue

            field_topup = profile_field_topup_enqueue.enqueue_field_topup_for_candidates(
                candidates=(recall_result.get("diagnostics") or {}).get(
                    "field_topup_candidates"
                ),
                session_id=int(session_id),
                staff=derived_staff,
                dry_run=bool(payload.get("field_topup_dry_run")),
            )
        except Exception:
            logger.warning(
                "field_topup_enqueue_failed session_id=%s",
                session_id,
                exc_info=True,
            )
            field_topup = {"status": "error", "reason": "field_topup_enqueue_failed"}

    result = profile_discovery_pipeline_stages.finalize_pipeline(
        session_id=int(session_id),
        query=planning.query,
        payload=payload,
        recall=recall,
        new_discovery=discovery.new_discovery,
        base_count=discovery.base_count,
        advance_result=advance_result,
        changed_ids=changed_ids,
        content_fit=content_fit,
        field_topup=field_topup,
        pipeline_status_resolver=_profile_advance_pipeline_status,
        deps=stage_deps,
    )
    if "search_mode" in payload:
        result["search_mode"] = search_mode
    return result
