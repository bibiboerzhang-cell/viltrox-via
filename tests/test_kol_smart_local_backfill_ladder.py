"""公测阻断 #1:本地腿一个正常查询返回 0,且没有任何兜底。

T 车道实测(4_smart_search)漏斗:检索 60 → 团队收藏排除砍 49 → 11 → 题材硬筛拒 6 → 5
→ 证据门拒 2 → 3 → 类型已知 1 → 资质 0 → 最终 0(缺 30)。本文件用同一组数字造夹具,断言:

* ``final >= min(30, 可用池)``——回填梯把「被收藏 / 题材不合 / 无产品内容 / 资料待核验」的人
  带标记补回来,外部测试者第一次搜索不再是空白;
* 诊断里有逐级回填账(``backfill_ladder``)与人话解释(``result_explanation``);
* 红线:回填人都带 ``backfill_tier`` / ``precision_match=False``,缺口(``shortfall``)仍只按
  精准命中计,不冒充命中;非 smart-local 车道零漂移;不写 fit_score。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.domains.kol import (
    profile_recall,
    profile_recall_backfill_ladder as ladder,
    profile_recall_qualification,
    recall_favorite_exclusion,
    targeted_local_recall,
)

TARGET = 30
RETRIEVED = 60
FAVORITED = 49
VERTICAL_REJECTED = 6
NO_EVIDENCE = 2
UNTYPED = 2

_PORTRAIT_BIO = "Studio portrait photographer shooting viltrox 35mm low-light portrait sessions"
_TRAVEL_BIO = "Daily travel stories with a viltrox 35mm low-light lens"
_NO_PRODUCT_PORTRAIT_BIO = "Studio portrait photographer"


def _row(
    item_id: int, *, bio: str, profile_type: str = "creator", country: str = "US"
) -> dict[str, Any]:
    return {
        "kol_pool_id": item_id,
        "handle": f"creator-{item_id}",
        "display_name": f"Creator {item_id}",
        "platform": "youtube",
        "profile_url": f"https://example.test/{item_id}",
        "followers": 20_000 + item_id,
        "country": country,
        "language": "en",
        "profile_type": profile_type,
        "creator_type_score": 90 if profile_type == "creator" else 10,
        "reviewer_type_score": 10,
        "profile_text": bio,
        "type_reason": "fixture",
        "bio": bio,
        "primary_topic": bio,
        "content_style": "review",
        "raw_platform_data": {},
    }


def _evidence(item_id: int, *, age_days: int = 10) -> dict[str, Any]:
    posted_at = datetime.now(timezone.utc) - timedelta(days=age_days)
    return {
        "representative_evidence": [
            {"title": "Field session", "content_url": f"https://example.test/video/{item_id}"}
        ],
        "latest_real_video": {
            "posted_at": posted_at.isoformat(),
            "evidence_type": "video",
            "content_url": f"https://example.test/video/latest-{item_id}",
            "source": "vkpi_kol_video_evidence.posted_at",
        },
    }


#: 被收藏的 49 人的构成:10 精准样、5 题材不合、3 无产品内容、2 类型未知、29 市场不符(硬拒,永不回填)。
_FAVORITED_MIX: tuple[tuple[int, str, str, str], ...] = (
    (10, _PORTRAIT_BIO, "creator", "US"),
    (5, _TRAVEL_BIO, "creator", "US"),
    (3, _NO_PRODUCT_PORTRAIT_BIO, "creator", "US"),
    (2, _PORTRAIT_BIO, "unknown", "US"),
    (29, _PORTRAIT_BIO, "creator", "DE"),
)
FAVORITED_PRECISE_LIKE = _FAVORITED_MIX[0][0]
FAVORITED_MARKET_MISMATCH = _FAVORITED_MIX[-1][0]


def _t_round_fixture() -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]], set[int]]:
    """按 T 车道漏斗造 60 人:49 被收藏;剩下 11 里 6 题材不合、2 无产品内容、2 类型未知、1 精准。"""

    rows: dict[int, dict[str, Any]] = {}
    visible = RETRIEVED - FAVORITED
    for item_id in range(1, visible + 1):
        if item_id <= VERTICAL_REJECTED:
            bio, profile_type = _TRAVEL_BIO, "creator"
        elif item_id <= VERTICAL_REJECTED + NO_EVIDENCE:
            bio, profile_type = _NO_PRODUCT_PORTRAIT_BIO, "creator"
        elif item_id <= VERTICAL_REJECTED + NO_EVIDENCE + UNTYPED:
            bio, profile_type = _PORTRAIT_BIO, "unknown"
        else:
            bio, profile_type = _PORTRAIT_BIO, "creator"
        rows[item_id] = _row(item_id, bio=bio, profile_type=profile_type)
    item_id = visible
    for count, bio, profile_type, country in _FAVORITED_MIX:
        for _ in range(count):
            item_id += 1
            rows[item_id] = _row(item_id, bio=bio, profile_type=profile_type, country=country)
    assert len(rows) == RETRIEVED
    evidence = {item_id: _evidence(item_id) for item_id in rows}
    favorited = set(range(visible + 1, RETRIEVED + 1))
    assert len(favorited) == FAVORITED
    return rows, evidence, favorited


def _install_recall(
    monkeypatch: pytest.MonkeyPatch,
    rows: dict[int, dict[str, Any]],
    evidence: dict[int, dict[str, Any]],
    favorited: set[int],
) -> None:
    monkeypatch.setenv("RECALL_LLM_RERANK_ENABLED", "0")
    monkeypatch.setattr(
        profile_recall,
        "resolve_query_text",
        lambda **_kwargs: (
            "viltrox 35mm low-light portrait",
            {"query_profile": "", "query_text_provided": True},
        ),
    )
    monkeypatch.setattr(
        profile_recall,
        "_pool_text_fallback_hits",
        lambda *_args, **_kwargs: [
            profile_recall.RecallHit(item_id, 1.0 - item_id / 10_000, f"point-{item_id}")
            for item_id in rows
        ],
    )
    monkeypatch.setattr(
        profile_recall,
        "_entry_rows",
        lambda ids: {item_id: dict(rows[item_id]) for item_id in ids if item_id in rows},
    )
    monkeypatch.setattr(
        profile_recall,
        "_evidence_summaries",
        lambda ids: {item_id: dict(evidence[item_id]) for item_id in ids if item_id in evidence},
    )
    monkeypatch.setattr(profile_recall, "_pool_rows_fallback", lambda _ids: {})
    monkeypatch.setattr(profile_recall, "_adoption_profile", lambda: {})

    def _exclude(hits: Any, **_kwargs: Any) -> tuple[list[Any], dict[str, Any]]:
        kept = [hit for hit in hits if hit.kol_pool_id not in favorited]
        excluded = sorted(hit.kol_pool_id for hit in hits if hit.kol_pool_id in favorited)
        return kept, recall_favorite_exclusion._diagnostics(considered=len(hits), excluded=excluded)

    monkeypatch.setattr(recall_favorite_exclusion, "exclude_favorited_hits", _exclude)


def _run_smart_local(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "query_text": "viltrox 35mm low-light portrait",
        "provider_free": True,
        "candidate_limit": RETRIEVED,
        "limit": TARGET,
        "creator_quota": 15,
        "reviewer_quota": 15,
        "allow_backfill": True,
        "filters": {"verticals": ["portrait"]},
        "required_product_evidence_terms": ["viltrox"],
        # 本文件按**严口径**(收藏隐藏 + 视频 45 天硬拒)复现 T 车道那次实测漏斗,
        # 因为回填梯的四级只有在「有人被藏起来 / 被判死」时才全部有人可捞。松绑口径
        # (产品默认)下同一批人从一开始就在主跑里,对照断言在 tests/test_search_relaxation.py。
        "local_qualification_policy": profile_recall_qualification.smart_local_policy(
            market="US",
            platforms=["youtube"],
            gate_mode="strict",
        ),
    }
    kwargs.update(overrides)
    return profile_recall.recall_kol_profiles(**kwargs)


def test_t_round_funnel_no_longer_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    rows, evidence, favorited = _t_round_fixture()
    _install_recall(monkeypatch, rows, evidence, favorited)

    result = _run_smart_local()

    diagnostics = result["diagnostics"]
    # 漏斗与 T 车道实测一致:60 → 收藏砍 49 → 11 → 题材拒 6 → 证据拒 2 → 3 → 精准 1。
    assert diagnostics["favorite_excluded_count"] == FAVORITED
    assert diagnostics["hard_filter_rejected_by"]["verticals"] == VERTICAL_REJECTED
    assert diagnostics["filtered_no_match_evidence"] == NO_EVIDENCE
    assert diagnostics["typed_candidate_count"] == 1
    assert diagnostics["unknown_type_candidate_count"] == UNTYPED
    precise = result["items"]
    backfill = result["backfill_items"]
    assert diagnostics["precise_count"] == len(precise) == 1
    available_pool = RETRIEVED
    assert len(precise) + len(backfill) >= min(TARGET, available_pool)
    assert len(precise) + len(backfill) == TARGET
    # 精准命中不带标记;回填人人带标记,不冒充命中。
    assert all(not ladder.is_backfill_item(item) for item in precise)
    assert all(ladder.is_backfill_item(item) for item in backfill)
    assert all(item["precision_match"] is False for item in backfill)
    assert all(item["counts_toward_target"] is False for item in backfill)
    assert all(item["selection_tier"].startswith("backfill_") for item in backfill)
    assert all(item["backfill_label"] for item in backfill)
    # 回填梯逐级记账:每一级补了几个、为什么没补。
    account = diagnostics["backfill_ladder"]
    assert account["schema"] == ladder.BACKFILL_LADDER_SCHEMA
    assert account["status"] == "filled"
    assert account["filled_total"] == len(backfill)
    assert sum(account["filled_by_tier"].values()) == len(backfill)
    assert [rung["tier"] for rung in account["rungs"]] == list(ladder.TIER_ORDER)
    assert account["filled_by_tier"][ladder.TIER_TEAM_FAVORITE] > 0
    assert account["filled_by_tier"][ladder.TIER_VERTICAL_RELAXED] > 0
    assert account["filled_by_tier"][ladder.TIER_EVIDENCE_RELAXED] > 0
    assert account["filled_by_tier"][ladder.TIER_QUALIFICATION_RELAXED] > 0
    # 题材放宽的人带 verticals 放宽标记;无产品内容的人证据留空(不伪造)。
    vertical_items = [i for i in backfill if i["backfill_tier"] == ladder.TIER_VERTICAL_RELAXED]
    assert vertical_items and all("verticals" in i["relaxed_filters"] for i in vertical_items)
    evidence_items = [i for i in backfill if i["backfill_tier"] == ladder.TIER_EVIDENCE_RELAXED]
    assert evidence_items and all(i["match_evidence"] == [] for i in evidence_items)
    favorite_items = [i for i in backfill if i["backfill_tier"] == ladder.TIER_TEAM_FAVORITE]
    assert len(favorite_items) == FAVORITED_PRECISE_LIKE
    assert all(i["kol_pool_id"] in favorited for i in favorite_items)
    # 被收藏且题材不合的人:两个放宽标记都带上。
    assert any(
        ladder.TIER_TEAM_FAVORITE in i["backfill_reasons"] for i in vertical_items
    )
    # 硬拒(市场不符)永不回填,只进「为什么没有更多」账。
    assert account["gaps"]["market_mismatch"] == FAVORITED_MARKET_MISMATCH
    assert all(i["kol_pool_id"] not in range(RETRIEVED - FAVORITED_MARKET_MISMATCH + 1, RETRIEVED + 1) for i in backfill)
    # 缺口只按精准命中计:补了 29 人也不能把「缺 29」说成「缺 0」。
    contract = result["local_qualification"]
    assert contract["shortfall"] == TARGET - len(precise)
    assert contract["precise_returned_count"] == len(precise)
    assert contract["backfill_returned_count"] == len(backfill)
    assert contract["backfill"]["filled_total"] == len(backfill)


def test_result_explanation_is_plain_language(monkeypatch: pytest.MonkeyPatch) -> None:
    rows, evidence, favorited = _t_round_fixture()
    _install_recall(monkeypatch, rows, evidence, favorited)

    explanation = _run_smart_local()["diagnostics"]["result_explanation"]

    assert explanation["schema"] == ladder.RESULT_EXPLANATION_SCHEMA
    assert explanation["requested"] == TARGET
    assert explanation["precise_count"] == 1
    assert explanation["backfill_count"] == TARGET - 1
    # 门面先报「找到几个人」再说凭什么:旧口径以「精准命中 0 人」开头,哪怕卡面上站着
    # 30 个人也读成「没搜到」——那正是「搜索越来越笨」的观感来源。
    assert explanation["headline"] == "为你找到 30 人:精准命中 1 人,另 29 人已标注入选原因"
    assert {entry["code"] for entry in explanation["backfill_reasons"]} == set(ladder.TIER_ORDER)
    assert {"code": "market_mismatch", "label": "不符合目标市场", "count": FAVORITED_MARKET_MISMATCH} in explanation["gaps"]
    assert explanation["favorited_by_team_hidden"] == FAVORITED - FAVORITED_PRECISE_LIKE
    blob = " ".join(
        str(value)
        for entry in [*explanation["backfill_reasons"], *explanation["gaps"]]
        for value in entry.values()
        if isinstance(value, str) and not value.islower()
    ) + explanation["headline"] + explanation["note"]
    for banned in ("LLM", "lexicon", "rule_v0", "qdrant", "词表", "embedding"):
        assert banned.lower() not in blob.lower()


def test_favorite_note_stops_claiming_nobody_filled_in(monkeypatch: pytest.MonkeyPatch) -> None:
    rows, evidence, favorited = _t_round_fixture()
    _install_recall(monkeypatch, rows, evidence, favorited)

    note = _run_smart_local()["diagnostics"]["favorite_exclusion_note"]

    assert "未用其他人补位" not in note
    assert note == (
        f"已排除 {FAVORITED} 个已被关注的人;精准命中 1 人,另补充 {TARGET - 1} 人"
        f"(已标注补充原因,其中 {FAVORITED_PRECISE_LIKE} 人为已被同事关注)。"
    )


def test_backfill_items_walk_through_privacy_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    rows, evidence, favorited = _t_round_fixture()
    marker = "PRIVATE_MARKER@example.test"
    for row in rows.values():
        row["email"] = marker
        row["raw_platform_data"] = {"business_email": marker}
    _install_recall(monkeypatch, rows, evidence, favorited)

    result = _run_smart_local()

    assert result["backfill_items"]
    for item in result["backfill_items"]:
        assert marker not in str(item)
        assert "raw_platform_data" not in item


def test_backfill_ladder_is_not_needed_when_precise_hits_fill_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = {item_id: _row(item_id, bio=_PORTRAIT_BIO) for item_id in range(1, 41)}
    evidence = {item_id: _evidence(item_id) for item_id in rows}
    _install_recall(monkeypatch, rows, evidence, favorited=set())

    result = _run_smart_local(candidate_limit=40)

    assert len(result["items"]) == TARGET
    assert result["backfill_items"] == []
    assert result["diagnostics"]["backfill_ladder"]["status"] == "not_needed"
    assert result["local_qualification"]["shortfall"] == 0
    assert result["diagnostics"]["result_explanation"]["headline"] == f"精准命中 {TARGET} 人"


def test_non_smart_local_lane_is_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    rows, evidence, favorited = _t_round_fixture()
    _install_recall(monkeypatch, rows, evidence, favorited)

    result = _run_smart_local(local_qualification_policy=None, allow_backfill=False, limit=10)

    assert result["backfill_items"] == []
    assert "backfill_ladder" not in result["diagnostics"]
    assert "result_explanation" not in result["diagnostics"]


def test_no_fit_score_is_written_anywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    rows, evidence, favorited = _t_round_fixture()
    _install_recall(monkeypatch, rows, evidence, favorited)

    result = _run_smart_local()

    for item in [*result["items"], *result["backfill_items"]]:
        assert "viltrox_fit_score" not in item
        assert "fit_score" not in item


# ── 定向 QueryCell 层:回填区排在精准命中之后,缺口只按精准计 ─────────────────


def _cell(index: int) -> dict[str, Any]:
    return {
        "query_cell_id": f"cell-{index}",
        "objective": "existing_evidence",
        "segment": f"segment-{index}",
        "segment_label": f"Segment {index}",
        "primary_query": f"segment {index} portrait creator",
        "platforms": ["youtube"],
        "round": 1,
        "raw_limit": 15,
        "required_evidence_groups": ["product_use_fit"],
        "brand_or_model_required": True,
    }


def _precise_item(item_id: int) -> dict[str, Any]:
    return {
        "kol_pool_id": item_id,
        "platform": "youtube",
        "handle": f"creator{item_id}",
        "bucket": "creator",
        "display_rank_score": 90.0 - item_id,
        "qualification_evidence": {"passed": True, "deferred": False, "rejection_reasons": []},
    }


def _backfill_item(item_id: int, tier: str) -> dict[str, Any]:
    item = {
        "kol_pool_id": item_id,
        "platform": "youtube",
        "handle": f"creator{item_id}",
        "bucket": "creator",
        "display_rank_score": 50.0 - item_id,
        "match_evidence": [],
        "qualification_evidence": {"passed": False, "rejection_reasons": ["low_relevance"]},
    }
    return ladder.mark_backfill_item(item, tier)


def _stub_recall(precise: int, backfill: int) -> Any:
    """每次调用(每个 cell)回自己的一段 id(第 1 次从 1 起,第 2 次从 1001 起……),避免跨 cell 撞人。"""

    calls: list[int] = []

    def recall(**_kwargs: Any) -> dict[str, Any]:
        base = 1000 * len(calls)
        calls.append(base)
        items = [_precise_item(base + item_id) for item_id in range(1, precise + 1)]
        backfill_items = [
            _backfill_item(base + 100 + offset, ladder.TIER_ORDER[offset % len(ladder.TIER_ORDER)])
            for offset in range(backfill)
        ]
        filled_by_tier = {tier: 0 for tier in ladder.TIER_ORDER}
        for item in backfill_items:
            filled_by_tier[item["backfill_tier"]] += 1
        return {
            "items": items,
            "backfill_items": backfill_items,
            "diagnostics": {"favorite_excluded_count": FAVORITED},
            "local_qualification": {
                "schema": "smart_local_qualified_v2",
                "policy": {"policy_version": 2},
                "evaluated_count": precise,
                "funnel": {"evaluated": precise},
                "rejected_by_reason": {},
                "ratio_policy": {"policy": "soft"},
                "backfill": {
                    "schema": ladder.BACKFILL_LADDER_SCHEMA,
                    "filled_total": backfill,
                    "filled_by_tier": filled_by_tier,
                    "gaps": {"market_mismatch": 3},
                },
            },
        }

    return recall


def _run_cells(precise: int, backfill: int, *, cells: int = 1) -> dict[str, Any]:
    return targeted_local_recall.execute_first_round_local_cells(
        query_cells=[_cell(index) for index in range(1, cells + 1)],
        search_brief={"objective": "existing_evidence"},
        base_kwargs={
            "candidate_limit": 500,
            "limit": TARGET,
            "creator_quota": TARGET,
            "reviewer_quota": 0,
            "required_product_evidence_terms": ["viltrox"],
            "local_qualification_policy": {"policy_version": 2},
        },
        recall=_stub_recall(precise, backfill),
        target=TARGET,
    )


def test_targeted_cells_fill_from_backfill_after_precise_hits() -> None:
    result = _run_cells(precise=1, backfill=40)

    items = result["items"]
    diagnostics = result["diagnostics"]
    assert len(items) == min(TARGET, 1 + 40) == diagnostics["final_count"]
    assert diagnostics["precise_count"] == 1
    assert diagnostics["backfill_count"] == TARGET - 1
    assert diagnostics["backfill_available_count"] == 40
    assert not ladder.is_backfill_item(items[0])
    assert all(ladder.is_backfill_item(item) for item in items[1:])
    assert all(item["counts_toward_target"] is False for item in items[1:])
    # 缺口 / 契约只按精准计。
    assert diagnostics["shortfall"] == TARGET - 1
    assert diagnostics["result_contract_satisfied"] is False
    contract = result["local_qualification"]
    assert contract["shortfall"] == TARGET - 1
    assert contract["qualified_returned_count"] == 1
    assert contract["backfill_returned_count"] == TARGET - 1
    assert contract["returned_count"] == TARGET
    account = contract["backfill"]
    assert account["filled_total"] == TARGET - 1
    assert sum(account["filled_by_tier"].values()) == TARGET - 1
    assert account["gaps"] == {"market_mismatch": 3}
    assert diagnostics["backfill_ladder"] == account
    explanation = diagnostics["result_explanation"]
    assert explanation["precise_count"] == 1
    assert explanation["backfill_count"] == TARGET - 1
    assert explanation["favorited_by_team_hidden"] == FAVORITED - account["filled_by_tier"][ladder.TIER_TEAM_FAVORITE]
    assert result["diagnostics"]["targeted_cell_runs"][0]["backfill_returned"] == 40


def test_targeted_cells_backfill_never_exceeds_available_pool() -> None:
    result = _run_cells(precise=0, backfill=5)

    assert len(result["items"]) == 5 == min(TARGET, 5)
    assert result["diagnostics"]["precise_count"] == 0
    assert result["diagnostics"]["shortfall"] == TARGET
    assert result["diagnostics"]["result_explanation"]["headline"] == "为你找到 5 人(均已标注入选原因,暂无精准命中)"


def test_targeted_cells_without_backfill_keep_legacy_shape() -> None:
    result = _run_cells(precise=15, backfill=0, cells=3)

    assert len(result["items"]) == TARGET
    assert result["diagnostics"]["backfill_count"] == 0
    assert result["diagnostics"]["shortfall"] == 0
    assert result["diagnostics"]["result_contract_satisfied"] is True
    assert result["local_qualification"]["backfill"]["status"] == "nothing_to_backfill"
