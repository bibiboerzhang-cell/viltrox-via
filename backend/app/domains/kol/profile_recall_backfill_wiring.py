"""回填梯与编排层之间的接线(条目物化 + 阶段回调 + 选人结果合并)。

编排层(``profile_recall_orchestration``)只做两件事:投影时把被拒候选记进储备账本、选人后
调 :func:`apply_backfill_ladder`。本模块把编排层的阶段能力(水合 / 投影)以回调形式转交给
:mod:`profile_recall_backfill_ladder`,并把回填结果合回 ``selection`` / 资质合同。

依赖方向:orchestration → wiring → ladder;wiring 不反向 import orchestration(水合 / 投影
两个阶段函数由编排层作为参数传入),环只减不增。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.domains.kol import profile_recall_backfill_ladder as _ladder
from app.domains.kol import search_relaxation as _relax
from app.domains.kol.profile_recall_orchestration_contract import RecallRequest

Hydrate = Callable[[RecallRequest, dict[str, Any], Any], dict[str, Any]]
Project = Callable[..., dict[str, Any]]


def annotate_projected_item(
    item: dict[str, Any],
    *,
    hit: Any,
    unknown_fields: list[str],
    vertical_reading: Any,
    deps: Any,
) -> None:
    retrieval_tier = (
        "backfill"
        if hit.qdrant_point_id == "pool_relevance_backfill"
        else str(hit.retrieval_tier or "backfill")
    )
    if retrieval_tier not in {"strict", "relaxed", "backfill"}:
        retrieval_tier = "relaxed"
    relaxed_filters: list[str] = []
    if retrieval_tier == "backfill":
        relaxed_filters = ["query_relevance"]
    elif retrieval_tier == "relaxed":
        relaxed_filters = ["factual_query_anchor"]
    item.update(
        {
            "match_tier": retrieval_tier,
            "filter_status": retrieval_tier,
            "relaxed_filters": relaxed_filters,
            "unknown_fields": unknown_fields,
            "vertical_tags": list(vertical_reading.verticals),
            "vertical_evidence": deps.vertical_explanations(vertical_reading),
        }
    )


def match_evidence_for(
    entry: Any,
    *,
    request: RecallRequest,
    context: dict[str, Any],
    deps: Any,
) -> list[dict[str, Any]]:
    """产品腿一个字不动;意图腿要几个证据由本次口径决定(松绑 1 / 严口径 2)。

    AND-2 的前提是候选行有 8 个可举证字段,而本地池里 content_style 填充率 0%、
    profile_text 与 type_reason 两列在表里根本不存在——八个里三个恒空。在线腿
    2026-08-25 已因同一理由降到 1,本地腿在松绑口径下同口径。
    """

    return deps.build_query_cell_match_evidence(
        entry.row,
        entry.evidence,
        context["resolved_text"],
        query_cell=request.targeted_query_cell,
        required_product_terms=context["safe_product_evidence_terms"],
        fallback_query_text=context["evidence_query_text"],
        min_intent_terms=_relax.min_intent_terms(request.local_qualification_policy),
    )


def materialize_item(
    entry: Any,
    field_evidence: list[dict[str, Any]],
    *,
    request: RecallRequest,
    context: dict[str, Any],
    deps: Any,
) -> dict[str, Any]:
    """把过闸(或回填梯放行)的候选做成完整条目。主跑与回填共用,零分叉。"""

    hit, row, evidence = entry.hit, entry.row, entry.evidence
    bucket = deps._bucket_for(row, request.mixed_policy)
    item = deps._format_item(
        hit,
        row,
        bucket,
        vector_weight=request.safe_vector_weight,
        type_weight=request.safe_type_weight,
        type_boost_enabled=bool(request.type_boost_enabled),
        evidence=evidence,
        persona_text=context["persona_text"],
        product_label=context["product_label"],
        video_leaning=context["video_leaning"],
    )
    if not request.allow_backfill:
        item["match_evidence"] = list(field_evidence)
        item["why_fit"] = deps.why_fit_from_match_evidence(field_evidence)
        item["candidate_facets"] = deps.candidate_facets(row, evidence)
    annotate_projected_item(
        item,
        hit=hit,
        unknown_fields=entry.unknown_fields,
        vertical_reading=entry.vertical_reading,
        deps=deps,
    )
    return item


def _ladder_phases(
    request: RecallRequest,
    context: dict[str, Any],
    retrieval: dict[str, Any],
    deps: Any,
    *,
    hydrate: Hydrate,
    project: Project,
) -> Any:
    """把编排层的阶段能力打包给回填梯(回填梯零反向依赖)。"""

    def _hydrate(hits: list[Any]) -> dict[str, Any]:
        return hydrate(request, {**retrieval, "hits": hits}, deps)

    def _project(hydration: dict[str, Any], reserve: Any) -> dict[str, Any]:
        return project(request, context, hydration, deps, reserve=reserve)

    def _rank(items: list[dict[str, Any]]) -> None:
        # 同一套排序公式,只是单独对回填区打分;回填区永远排在精准命中之后。
        deps.apply_robust_ranking(items)
        deps._assign_business_buckets(items, request.normalized_bucket_policy)
        items.sort(key=deps.ranking_key, reverse=True)

    def _qualify(
        candidates: list[dict[str, Any]],
        capacity: int,
        excluded_aliases: set[str],
        rows: dict[int, dict[str, Any]],
        evidence: dict[int, dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        items, _buckets, contract = deps.qualify_local_candidates(
            buckets={
                "creator": [item for item in candidates if item.get("bucket") != "reviewer"],
                "reviewer": [item for item in candidates if item.get("bucket") == "reviewer"],
            },
            rows_by_id=rows,
            evidence_by_id=evidence,
            policy=dict(request.local_qualification_policy or {}),
            creator_quota=request.safe_creator_quota,
            reviewer_quota=request.safe_reviewer_quota,
            target_count=capacity,
            excluded_identity_aliases=set(excluded_aliases),
        )
        return items, contract

    return _ladder.LadderPhases(
        hydrate=_hydrate,
        project=_project,
        match_evidence=lambda entry: match_evidence_for(
            entry, request=request, context=context, deps=deps
        ),
        materialize=lambda entry, field_evidence: materialize_item(
            entry, field_evidence, request=request, context=context, deps=deps
        ),
        rank=_rank,
        qualify=_qualify,
        ranking_key=deps.ranking_key,
    )


def _ledger_gaps(ledger: Any, ladder_gaps: dict[str, Any]) -> dict[str, int]:
    """「为什么没有更多」:硬筛(题材除外,它可放宽)+ 地区 / 触达闸 + 梯上记的硬拒。"""

    gaps = {str(key): int(value or 0) for key, value in (ladder_gaps or {}).items()}
    for name, count in (getattr(ledger, "rejected_by", None) or {}).items():
        if name != "verticals":
            gaps[name] = max(int(count), gaps.get(name, 0))
    for name in ("excluded_region", "low_reach"):
        count = int(getattr(ledger, name, 0) or 0)
        if count:
            gaps[name] = count
    return gaps


def _annotate_contract_backfill(
    contract: dict[str, Any],
    precise: list[dict[str, Any]],
    outcome: Any,
    gaps: dict[str, int],
) -> None:
    """资质合同:``returned_count`` / ``shortfall`` 只认精准;回填账单独一块,不混。"""

    contract["precise_returned_count"] = len(precise)
    contract["backfill_returned_count"] = len(outcome.items)
    # 既有 funnel 字典一字不动(契约测试整体比对);回填计数只住在 backfill 块里。
    contract["backfill"] = {**outcome.diagnostics, "gaps": dict(gaps)}
    # 回填区的过闸证明单列,不混进 gate_evidence(那一列的口径是「精准返回的人」)。
    contract["backfill_gate_evidence"] = [
        item["qualification_evidence"]
        for item in outcome.items
        if isinstance(item.get("qualification_evidence"), dict)
    ]


def _qualified_returned(contract: Any, *, fallback: int) -> int:
    """精准命中的诚实口径:资质门真判 ``passed`` 的那些人。

    非回填区里还坐着「活跃度未知、占位但不计入目标」的人(``deferred``)。他们有自己的
    卡面标注,但把他们并进「精准命中 N 人」就是把命中数说虚了——门面只认资质合同里的
    ``qualified_returned_count``。
    """

    if isinstance(contract, dict) and contract.get("qualified_returned_count") is not None:
        try:
            return max(0, int(contract["qualified_returned_count"]))
        except (TypeError, ValueError):
            return int(fallback)
    return int(fallback)


def apply_backfill_ladder(
    request: RecallRequest,
    context: dict[str, Any],
    retrieval: dict[str, Any],
    hydration: dict[str, Any],
    projection: dict[str, Any],
    selection: dict[str, Any],
    deps: Any,
    *,
    hydrate: Hydrate,
    project: Project,
) -> None:
    """精准命中不够 30 时按梯回填;回填条目排在精准命中之后,缺口仍只按精准计。"""

    outcome = _ladder.run_backfill_ladder(
        target=request.safe_limit,
        selected=selection["items"],
        projection=projection,
        hydration=hydration,
        reserve=projection["reserve"],
        favorited_hits=list(retrieval.get("favorited_hits") or []),
        phases=_ladder_phases(
            request, context, retrieval, deps, hydrate=hydrate, project=project,
        ),
        soft_reasons=_ladder.soft_reasons_for_policy(request.local_qualification_policy),
        evidence_reasons=_ladder.evidence_reasons_for_policy(
            request.local_qualification_policy
        ),
    )
    precise = list(selection["items"])
    # 回填区单独成列(``backfill_items``),``items`` 仍只装精准命中:既有「闸在限额之前、
    # 缺口诚实」的契约一字不改;定向 QueryCell 层(targeted_local_recall)再把两区合并展示。
    selection["backfill_items"] = list(outcome.items)
    selection["backfill_ladder"] = outcome.diagnostics
    gaps = _ledger_gaps(projection["ledger"], outcome.diagnostics.get("gaps") or {})
    contract = selection.get("local_qualification")
    if isinstance(contract, dict):
        _annotate_contract_backfill(contract, precise, outcome, gaps)
    qualified_returned = _qualified_returned(contract, fallback=len(precise))
    selection["result_explanation"] = _ladder.explain_result(
        requested=request.safe_limit,
        precise_count=qualified_returned,
        deferred_count=max(0, len(precise) - qualified_returned),
        backfill_by_tier=outcome.diagnostics.get("filled_by_tier"),
        gaps=gaps,
        favorite_excluded=int(retrieval["favorite_exclusion"].get("excluded_count") or 0),
        # 逐条数返回里带「已被同事关注」标记的人:天然按身份去重,不会虚高。
        favorite_annotated=sum(
            1 for item in [*precise, *outcome.items] if _relax.is_team_favorite(item)
        ),
    )


def annotate_favorite_note_after_backfill(diagnostics: dict[str, Any]) -> dict[str, Any]:
    """收藏排除的人话注脚在回填之后要改口:不能再说「未用其他人补位」。

    在 :func:`recall_favorite_exclusion.annotate_shortfall` 之后调用;没回填时一字不改。
    """

    ladder_block = diagnostics.get("backfill_ladder")
    filled = int((ladder_block or {}).get("filled_total") or 0) if isinstance(ladder_block, dict) else 0
    excluded = int(diagnostics.get("favorite_excluded_count") or 0)
    if not filled or not excluded:
        return diagnostics
    favorites_back = int((ladder_block.get("filled_by_tier") or {}).get(_ladder.TIER_TEAM_FAVORITE) or 0)
    precise = int(diagnostics.get("precise_count") or 0)
    note = f"已排除 {excluded} 个已被关注的人;精准命中 {precise} 人,另补充 {filled} 人(已标注补充原因"
    if favorites_back:
        note += f",其中 {favorites_back} 人为已被同事关注"
    diagnostics["favorite_exclusion_note"] = note + ")。"
    return diagnostics


__all__ = [
    "annotate_favorite_note_after_backfill",
    "annotate_projected_item",
    "apply_backfill_ladder",
    "match_evidence_for",
    "materialize_item",
]
