"""定向路径「补充人选」的对外契约:顶层数组不许骗人,逐人标记必须活到回放。

三件事钉死:
1. ``backfill_items`` 顶层键在定向路径恒空 —— 补充人选已经并进 ``items`` 并逐人盖章,
   顶层留着的是 ``{**first}`` 继承的第一个 cell 残值(实测 items=20 而残值=15),
   照它渲染会展示**错的人**;
2. 补充人选带着标记走完落库投影(会话白名单),从历史打开会话时仍分得清谁是补充的;
3. 门面要印的「另有 N 人已被同事关注」按身份去重,多 cell 不虚高。
"""
from __future__ import annotations

from typing import Any

from app.domains.kol import (
    profile_recall_backfill_ladder as ladder,
    search_sessions_attach,
    search_sessions_recall_fields,
    targeted_local_recall,
)

BACKFILL_MARK_FIELDS = (
    "backfill_tier",
    "backfill_label",
    "backfill_reasons",
    "precision_match",
    "counts_toward_target",
)


def _cell(index: int) -> dict[str, Any]:
    return {
        "query_cell_id": f"cell-{index}",
        "objective": "existing_evidence",
        "segment": f"segment-{index}",
        "segment_label": f"Segment {index}",
        "primary_query": f"segment {index} viltrox creator",
        "platforms": ["youtube"],
        "round": 1,
        "raw_limit": 15,
        "required_evidence_groups": ["product_use_fit"],
        "brand_or_model_required": True,
    }


def _row(item_id: int, *, passed: bool, score: float = 90.0) -> dict[str, Any]:
    return {
        "kol_pool_id": item_id,
        "platform": "youtube",
        "handle": f"creator{item_id}",
        "display_name": f"Creator {item_id}",
        "bucket": "creator",
        "display_rank_score": score,
        "qualification_evidence": {
            "passed": passed,
            "deferred": False,
            "rejection_reasons": [] if passed else ["low_relevance"],
        },
    }


def _backfill_row(item_id: int, tier: str, *, score: float = 50.0) -> dict[str, Any]:
    return ladder.mark_backfill_item(_row(item_id, passed=False, score=score), tier)


def _contract(evaluated: int) -> dict[str, Any]:
    return {
        "schema": "smart_local_qualified_v2",
        "policy": {"policy_version": 2},
        "evaluated_count": evaluated,
        "funnel": {"evaluated": evaluated},
        "rejected_by_reason": {},
        "ratio_policy": {"policy": "soft"},
    }


def _base_kwargs(target: int) -> dict[str, Any]:
    return {
        "candidate_limit": 500,
        "limit": target,
        "creator_quota": target,
        "reviewer_quota": 0,
        "required_product_evidence_terms": ["viltrox"],
        "local_qualification_policy": {"policy_version": 2},
    }


def _cell_result(
    *,
    precise_ids: list[int],
    backfill_ids: list[int],
    favorite_excluded: list[int],
    excluded_sample: list[int] | None = None,
    tier: str = ladder.TIER_TEAM_FAVORITE,
    backfill_score: float = 50.0,
) -> dict[str, Any]:
    sample = excluded_sample if excluded_sample is not None else favorite_excluded
    return {
        "items": [_row(item_id, passed=True) for item_id in precise_ids],
        "backfill_items": [
            _backfill_row(item_id, tier, score=backfill_score) for item_id in backfill_ids
        ],
        "local_qualification": _contract(len(precise_ids) + len(backfill_ids)),
        "diagnostics": {
            "favorite_excluded_count": len(favorite_excluded),
            "favorite_exclusion": {
                "schema": "recall_favorite_exclusion_v1",
                "available": True,
                "excluded_count": len(favorite_excluded),
                "excluded_ids": list(sample),
                "excluded_ids_truncated": len(sample) < len(favorite_excluded),
            },
        },
    }


def _run(results: list[dict[str, Any]], *, target: int = 6) -> dict[str, Any]:
    pending = list(results)

    def recall(**_kwargs: Any) -> dict[str, Any]:
        return pending.pop(0) if pending else {"items": []}

    return targeted_local_recall.execute_first_round_local_cells(
        query_cells=[_cell(index) for index in range(1, len(results) + 1)],
        search_brief={"objective": "existing_evidence"},
        base_kwargs=_base_kwargs(target),
        recall=recall,
        target=target,
    )


def test_top_level_backfill_items_is_never_the_first_cell_residue() -> None:
    """顶层 ``backfill_items`` 不再是第一个 cell 的残值:定向路径恒空,人在 items 里。"""

    first_cell = _cell_result(
        precise_ids=[1, 2],
        backfill_ids=[901, 902, 903],
        favorite_excluded=[],
    )
    residue_ids = [item["kol_pool_id"] for item in first_cell["backfill_items"]]
    result = _run(
        [
            first_cell,
            _cell_result(
                precise_ids=[3],
                backfill_ids=[904],
                favorite_excluded=[],
                backfill_score=99.0,
            ),
        ],
    )

    assert residue_ids == [901, 902, 903], "前提:第一个 cell 的召回结果里确实有回填人(残值来源)"
    assert result["backfill_items"] == []
    supplement_ids = [item["kol_pool_id"] for item in result["items"] if ladder.is_backfill_item(item)]
    supplements = [item for item in result["items"] if ladder.is_backfill_item(item)]
    # 真正选中的是排名更高的 904,而残值里既没有他、又多出一个没被选中的 903。
    assert supplement_ids != residue_ids
    assert 904 in supplement_ids and 903 not in supplement_ids
    # 补充人选一个不少地在 items 里,数量与诊断口径一致(不是第三批人)。
    assert len(supplements) == result["diagnostics"]["backfill_count"] > 0
    assert result["diagnostics"]["precise_count"] == 3
    assert result["diagnostics"]["returned_count"] == len(result["items"])
    assert len(result["items"]) == result["diagnostics"]["precise_count"] + len(supplements)


def test_supplement_rows_carry_every_mark_needed_to_tell_them_apart() -> None:
    """每个补充人选都带全五个标记,不冒充精准命中;精准命中一个标记都不带。"""

    result = _run(
        [
            _cell_result(precise_ids=[1], backfill_ids=[901, 902], favorite_excluded=[]),
        ],
    )

    supplements = [item for item in result["items"] if ladder.is_backfill_item(item)]
    precise = [item for item in result["items"] if not ladder.is_backfill_item(item)]
    assert supplements and precise
    for item in supplements:
        assert item["precision_match"] is False
        assert item["counts_toward_target"] is False
        assert item["backfill_label"] == ladder.TIER_LABELS[item["backfill_tier"]]
        assert item["backfill_reasons"]
        assert item["selection_tier"].startswith(ladder.BACKFILL_SELECTION_TIER_PREFIX)
    for item in precise:
        assert not any(item.get(field) for field in ("backfill_tier", "backfill_label"))
    # 缺口只按精准命中计:补充的人补不平缺口,也不把结果合同说成已满足。
    assert result["diagnostics"]["shortfall"] == 6 - 1
    assert result["diagnostics"]["result_contract_satisfied"] is False


def test_backfill_marks_survive_the_session_replay_whitelist() -> None:
    """白名单里有这五个键,补充人选落库后仍分得清 —— 少一个,回放就成了「一模一样」。"""

    fields = search_sessions_recall_fields._RECALL_SESSION_PAYLOAD_FIELDS
    for field in BACKFILL_MARK_FIELDS:
        assert field in fields, f"会话回放白名单缺 {field}"

    result = _run(
        [_cell_result(precise_ids=[1], backfill_ids=[901], favorite_excluded=[])],
    )
    rows, source, _count = search_sessions_attach._recall_source_items(result)
    assert source == "canonical_items"
    supplements = [row for row in rows if ladder.is_backfill_item(row)]
    assert supplements, "定向路径的补充人选必须走进落库源(在 items 里),否则回放后永久消失"
    payload = search_sessions_attach._recall_session_payload(
        supplements[0],
        bucket="creator",
        replay_complete=True,
        replay_source="canonical_items",
    )
    for field in BACKFILL_MARK_FIELDS:
        assert field in payload
    assert payload["precision_match"] is False
    assert payload["backfill_label"] == ladder.TIER_LABELS[ladder.TIER_TEAM_FAVORITE]


def test_favorited_hidden_count_is_deduped_across_cells() -> None:
    """同一批被关注的人被三个 cell 各排除一次,门面上只能算一次。"""

    excluded = [11, 12, 13, 14]
    result = _run(
        [
            _cell_result(precise_ids=[1], backfill_ids=[], favorite_excluded=excluded),
            _cell_result(precise_ids=[2], backfill_ids=[], favorite_excluded=excluded),
            _cell_result(precise_ids=[3], backfill_ids=[], favorite_excluded=excluded),
        ],
    )

    explanation = result["diagnostics"]["result_explanation"]
    assert explanation["schema"] == ladder.RESULT_EXPLANATION_SCHEMA
    assert explanation["favorited_by_team_hidden"] == len(excluded)


def test_favorited_hidden_count_never_overstates_when_sample_is_truncated() -> None:
    """身份样本被截断(拿不全是谁)时退回单 cell 最大值:宁可少说,不虚报。"""

    result = _run(
        [
            _cell_result(
                precise_ids=[1],
                backfill_ids=[],
                favorite_excluded=list(range(100, 130)),
                excluded_sample=[100, 101],
            ),
            _cell_result(
                precise_ids=[2],
                backfill_ids=[],
                favorite_excluded=list(range(100, 120)),
                excluded_sample=[100],
            ),
        ],
    )

    hidden = result["diagnostics"]["result_explanation"]["favorited_by_team_hidden"]
    assert hidden == 30
    assert hidden < 30 + 20


def test_result_explanation_speaks_human_language_only() -> None:
    """门面文案不许出现内部术语(它会被原样印在卡面上)。"""

    result = _run(
        [_cell_result(precise_ids=[1], backfill_ids=[901, 902], favorite_excluded=[7])],
    )

    explanation = result["diagnostics"]["result_explanation"]
    text = " ".join(
        [
            str(explanation.get("headline") or ""),
            str(explanation.get("note") or ""),
            *[str(entry.get("label") or "") for entry in explanation.get("backfill_reasons") or []],
            *[str(entry.get("label") or "") for entry in explanation.get("gaps") or []],
        ]
    ).lower()
    for banned in ("llm", "lexicon", "rule_v0", "embedding", "qdrant", "apify", "backfill", "tier", "词表"):
        assert banned not in text
