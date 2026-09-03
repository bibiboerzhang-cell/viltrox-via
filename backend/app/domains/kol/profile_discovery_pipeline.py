"""Queued smart-search profile advance pipeline.

This facade intentionally retains the established monkeypatch/runtime binding
seams. Stage implementations live in focused leaf modules so orchestration
order stays visible without one function owning every policy branch.
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.domains.kol import (
    derived_job_actor,
    profile_discovery_evidence,
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


async def execute_smart_search_profile_advance_pipeline(
    *,
    session_id: int,
    payload: dict[str, Any],
    provider_actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a queued text recall/new-discovery/profile-advance pipeline."""

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
    # Keep this call in the facade: it is the provider-free recall seam used by
    # the runtime compatibility binder and the stage-order contract tests.
    recall_result = targeted_search_runtime.execute_local_search(
        context=recall_setup.context,
        recall_kwargs=recall_setup.recall_kwargs,
        recall=profile_recall.recall_kol_profiles,
    )
    recall_result = filter_recall_result_platforms(
        recall_result,
        recall_setup.recall_filters.get("platforms"),
    )
    recall_result = filter_recall_result_market(
        recall_result,
        planning.operator_market,
    )
    recall_result = profile_recall_qualification.project_smart_local_result(recall_result)
    recall = profile_discovery_pipeline_stages.attach_recall(
        session_id=int(session_id),
        payload=payload,
        recall_result=recall_result,
        deps=stage_deps,
    )

    discovery = await profile_discovery_pipeline_online.run_discovery(
        profile_discovery_pipeline_online.DiscoveryRequest(
            session_id=int(session_id),
            query=planning.query,
            payload=payload,
            operator_anchor=planning.operator_anchor,
            resolved_platforms=recall_setup.resolved_platforms,
            normalized_market=planning.operator_market,
            followers_min=recall_setup.followers_min,
            followers_max=recall_setup.followers_max,
            follower_source=recall_setup.follower_source,
            follower_filter=recall_setup.follower_filter,
            query_cells=recall_setup.query_cells,
            query_cells_omitted=recall_setup.query_cells_omitted,
            base_count=recall.base_count,
            advance_limit=recall.advance_limit,
        ),
        discover=discover_new_creators,
        annotate_priority=_annotate_new_priority,
        deps=_online_dependencies(),
    )

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

    return profile_discovery_pipeline_stages.finalize_pipeline(
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
