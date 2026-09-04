"""搜索松绑口径:证据不足 / 同事已关注 / 视频陈旧不再硬杀,改成排序惩罚 + 诚实标注。

产品裁决(用户原话「以前搜索都能用,限制搜索越烂越笨了」)把口径从「证明不了相关就不给人」
改成「把找到的最好的人给出来,并诚实标注凭什么」。本文件钉死三件事:

* **松绑真的松了**:同一批候选,严口径判死的人在松绑口径下带着标注回到结果里;
* **该硬的仍然硬**:操作员显式勾选的筛选、地区规避、官号 / 零售 / 重复,一个都没放宽;
* **一个开关能回去**:``strict_gates=true`` 逐字恢复 2026-08 的行为,回执里写着键名与默认值。

严口径那一侧的完整契约在 tests/test_kol_smart_local_activity_deferral.py 与
tests/test_kol_smart_local_backfill_ladder.py(两个文件都已显式传 ``gate_mode="strict"``),
两边互为对照:同一组夹具,两种口径,两套断言。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.domains.kol import (
    profile_recall,
    profile_recall_backfill_ladder as ladder,
    profile_recall_match_evidence,
    profile_recall_qualification as qualification,
    recall_favorite_exclusion,
    search_relaxation as relax,
)


NOW = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)
_BIO = "Independent portrait photographer reviewing viltrox 35mm lenses in the field"


# ---------------------------------------------------------------------------
# 开关合同:键名、默认值、以及「没写就是不改」
# ---------------------------------------------------------------------------


def test_default_is_relaxed_and_one_body_key_takes_it_back() -> None:
    assert relax.DEFAULT_MODE == relax.MODE_RELAXED
    assert relax.resolve_mode({}) == relax.MODE_RELAXED
    assert relax.resolve_mode(None) == relax.MODE_RELAXED
    assert relax.resolve_mode({relax.BODY_STRICT_KEY: True}) == relax.MODE_STRICT
    assert relax.resolve_mode({relax.BODY_STRICT_KEY: "true"}) == relax.MODE_STRICT
    # 读不懂的值不许悄悄收紧,也不许悄悄放宽:一律回到默认。
    assert relax.resolve_mode({relax.BODY_STRICT_KEY: "maybe"}) == relax.MODE_RELAXED
    assert relax.resolve_mode({relax.POLICY_KEY: "wide-open"}) == relax.MODE_RELAXED


def test_policy_without_the_key_stays_strict_so_untouched_lanes_do_not_drift() -> None:
    """八月那批闸的病根是「默认悄悄变了」——这里不许重犯。

    没有策略字典、或者字典里根本没有 ``gate_mode``,说明这条车道从没接入松绑合同,
    那就一个字节都不改它的行为。
    """

    for absent in (None, {}, {"market": "us"}, "not-a-dict"):
        assert relax.policy_mode(absent) == relax.MODE_STRICT
        assert relax.is_relaxed(absent) is False
        assert relax.hide_team_favorites(absent) is True
        assert relax.min_intent_terms(absent) == relax.STRICT_MIN_INTENT_TERMS
        assert relax.relaxable_reasons(absent) == frozenset()


def test_hide_team_favorites_is_an_independent_knob() -> None:
    """想把同事关注过的人藏起来,不必连带把另外两道闸也收紧。"""

    relaxed = qualification.smart_local_policy()
    assert relaxed[relax.HIDE_FAVORITES_POLICY_KEY] is False
    assert relax.hide_team_favorites(relaxed) is False

    hidden = qualification.smart_local_policy(hide_team_favorites=True)
    assert relax.hide_team_favorites(hidden) is True
    # 藏起来了,但视频窗口与意图腿仍是松绑口径。
    assert hidden["max_video_age_days"] == relax.RELAXED_MAX_VIDEO_AGE_DAYS
    assert relax.min_intent_terms(hidden) == relax.RELAXED_MIN_INTENT_TERMS

    body_mode = relax.resolve_mode({relax.BODY_HIDE_FAVORITES_KEY: True})
    assert body_mode == relax.MODE_RELAXED
    assert relax.resolve_hide_team_favorites(
        {relax.BODY_HIDE_FAVORITES_KEY: True}, mode=body_mode
    ) is True
    assert relax.resolve_hide_team_favorites({}, mode=relax.MODE_STRICT) is True


def test_strict_policy_restores_every_august_default() -> None:
    strict = qualification.smart_local_policy(gate_mode="strict")
    assert strict[relax.POLICY_KEY] == relax.MODE_STRICT
    assert strict["max_video_age_days"] == qualification.SMART_LOCAL_MAX_VIDEO_AGE_DAYS == 45
    assert strict[relax.HIDE_FAVORITES_POLICY_KEY] is True
    assert relax.min_intent_terms(strict) == 2
    assert relax.relaxable_reasons(strict) == frozenset()

    relaxed = qualification.smart_local_policy()
    assert relaxed["max_video_age_days"] == 365
    assert relax.min_intent_terms(relaxed) == 1
    assert relax.relaxable_reasons(relaxed) == {"latest_video_stale"}
    # 除这三项外两个口径逐字相同 —— 松绑不许顺手改别的。
    ignored = {relax.POLICY_KEY, relax.HIDE_FAVORITES_POLICY_KEY, "max_video_age_days"}
    assert {k: v for k, v in strict.items() if k not in ignored} == {
        k: v for k, v in relaxed.items() if k not in ignored
    }


def test_receipt_names_every_switch_and_its_default() -> None:
    receipt = relax.relaxation_receipt(qualification.smart_local_policy())
    assert receipt["schema"] == relax.RELAXATION_SCHEMA
    assert receipt["mode"] == "relaxed"
    assert receipt["default_mode"] == "relaxed"
    assert receipt["strict_switch"]["body_key"] == "strict_gates"
    assert receipt["strict_switch"]["default"] is False
    assert receipt["strict_switch"]["value"] is False
    assert receipt["hide_team_favorites"]["body_key"] == "hide_team_favorites"
    assert receipt["hide_team_favorites"]["value"] is False
    assert receipt["max_video_age_days"] == {
        "policy_key": "max_video_age_days",
        "strict": 45,
        "relaxed": 365,
        "value": 365,
    }
    assert receipt["min_intent_terms"] == {"strict": 2, "relaxed": 1, "value": 1}
    assert receipt["relaxable_reasons"] == ["latest_video_stale"]

    strict_receipt = relax.relaxation_receipt(
        qualification.smart_local_policy(gate_mode="strict")
    )
    assert strict_receipt["strict_switch"]["value"] is True
    assert strict_receipt["max_video_age_days"]["value"] == 45
    assert strict_receipt["min_intent_terms"]["value"] == 2
    assert strict_receipt["relaxable_reasons"] == []


def test_only_video_recency_was_ever_downgraded() -> None:
    """松绑的边界写死在代码里,不靠记忆:能降的只有「视频陈旧」这一条。"""

    assert relax.RELAXABLE_QUALIFICATION_REASONS == frozenset({"latest_video_stale"})
    for guarded in (
        "platforms",
        "countries",
        "languages",
        "followers_min",
        "followers_max",
        "gear_content",
        "excluded_region",
        "account_own_brand",
        "account_brand_official",
        "account_retailer",
        "account_garbage",
        "duplicate_canonical_identity",
    ):
        assert guarded in relax.NEVER_RELAXED
        assert guarded not in relax.RELAXABLE_QUALIFICATION_REASONS
    # 回填梯的软原因表:松绑只多了「视频陈旧」,其余一字不动。
    relaxed_soft = ladder.soft_reasons_for_policy(qualification.smart_local_policy())
    strict_soft = ladder.soft_reasons_for_policy(
        qualification.smart_local_policy(gate_mode="strict")
    )
    assert relaxed_soft - strict_soft == {"latest_video_stale"}
    assert strict_soft - relaxed_soft == frozenset()
    assert ladder.evidence_reasons_for_policy(
        qualification.smart_local_policy(gate_mode="strict")
    ) == ladder.EVIDENCE_ONLY_REASONS


def test_session_snapshot_records_which_mode_produced_it() -> None:
    """一条老会话到底是宽口径还是严口径搜出来的,回放时必须读得出来,不许靠猜。"""

    from app.domains.kol.search_sessions_attach import _safe_local_qualification

    for mode, window, hidden in (("relaxed", 365, False), ("strict", 45, True)):
        _selected, contract = _qualify(
            [_item(1)], {1: _row(1)}, {1: _video(5)}, gate_mode=mode
        )
        policy = _safe_local_qualification(contract)["policy"]
        assert policy[relax.POLICY_KEY] == mode
        assert policy["max_video_age_days"] == window
        assert policy[relax.HIDE_FAVORITES_POLICY_KEY] is hidden


# ---------------------------------------------------------------------------
# 资质门:陈旧从硬杀降级成标注,窗口之外仍然是判决
# ---------------------------------------------------------------------------


def _item(item_id: int, *, rank: float = 1.0, evidence: bool = True) -> dict[str, Any]:
    return {
        "kol_pool_id": item_id,
        "handle": f"creator-{item_id}",
        "channel_name": f"Creator {item_id}",
        "platform": "youtube",
        "bucket": "creator",
        "display_rank_score": rank,
        "recall_rank_score": rank,
        "match_evidence": [{"field": "bio", "term": "lens"}] if evidence else [],
    }


def _row(item_id: int, **overrides: Any) -> dict[str, Any]:
    row = {
        "kol_pool_id": item_id,
        "followers": 5_000,
        "country": "US",
        "language": "en",
        "profile_type": "creator",
        "platform": "youtube",
        "bio": _BIO,
        "raw_platform_data": {},
    }
    row.update(overrides)
    return row


def _video(age_days: float) -> dict[str, Any]:
    return {
        "latest_real_video": {
            "posted_at": (NOW - timedelta(days=age_days)).isoformat(),
            "evidence_type": "video",
            "is_active": True,
            "content_url": "https://www.youtube.com/watch?v=auditable",
            "source": "vkpi_kol_video_evidence.posted_at",
        }
    }


def _qualify(
    items: list[dict[str, Any]],
    rows: dict[int, dict[str, Any]],
    evidence: dict[int, dict[str, Any]],
    *,
    gate_mode: str = "relaxed",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected, _buckets, contract = qualification.qualify_local_candidates(
        buckets={"creator": items, "reviewer": []},
        rows_by_id=rows,
        evidence_by_id=evidence,
        policy=qualification.smart_local_policy(
            market="US", platforms=["youtube"], gate_mode=gate_mode
        ),
        creator_quota=30,
        reviewer_quota=0,
        as_of=NOW,
    )
    return selected, contract


def test_stale_creator_is_returned_and_labelled_instead_of_rejected() -> None:
    """同一个人、同一份资料:严口径判死,松绑口径带着「近期没有更新视频」回到结果里。"""

    args = ([_item(1), _item(2)], {1: _row(1), 2: _row(2)}, {1: _video(5), 2: _video(200)})

    strict_selected, strict_contract = _qualify(*args, gate_mode="strict")
    assert [item["kol_pool_id"] for item in strict_selected] == [1]
    assert strict_contract["rejected_by_reason"] == {"latest_video_stale": 1}

    selected, contract = _qualify(*args, gate_mode="relaxed")
    assert [item["kol_pool_id"] for item in selected] == [1, 2]
    assert contract["rejected_by_reason"] == {}
    assert contract["qualified_returned_count"] == 2
    widened = selected[1]
    assert widened["qualification_evidence"]["passed"] is True
    # 卡面必须自己说清楚凭什么在这儿(门面只说人话)。
    assert widened["activity_recency_note"] == "近期没有更新视频"
    assert "近期没有更新视频" in widened["selection_notes"]
    # 45 天内的那位没有被无端盖章。
    assert "activity_recency_note" not in selected[0]


def test_creator_beyond_the_relaxed_window_is_still_rejected_but_recoverable() -> None:
    """松绑不是不设限:400 天仍然过不了闸,只是从此可以被回填梯带标注捞回来。"""

    selected, contract = _qualify(
        [_item(1), _item(2)],
        {1: _row(1), 2: _row(2)},
        {1: _video(5), 2: _video(400)},
    )
    assert [item["kol_pool_id"] for item in selected] == [1]
    assert contract["rejected_by_reason"] == {"latest_video_stale": 1}
    assert "latest_video_stale" in ladder.soft_reasons_for_policy(
        qualification.smart_local_policy()
    )
    assert ladder.GAP_LABELS["latest_video_stale"] == "近期没有更新视频"


def test_ranking_puts_the_strict_window_ahead_of_the_widened_one() -> None:
    """排序惩罚是分区,不是新评分:严口径也能过的人排在只因放宽才进来的人之前。"""

    selected, _contract = _qualify(
        [_item(1, rank=0.1), _item(2, rank=0.9)],
        {1: _row(1), 2: _row(2)},
        {1: _video(40), 2: _video(200)},
    )
    # 两人都在 30 天「最近更新」桶之外,分数高的却排在后面 —— 靠的正是新的窗口分区。
    assert [item["kol_pool_id"] for item in selected] == [1, 2]
    assert all("viltrox_fit_score" not in item for item in selected)


def test_hard_exclusions_stay_hard_in_relaxed_mode() -> None:
    """松绑不碰硬拒:市场不符 / 粉丝不足 / 平台不符 一个都没放行。"""

    items = [_item(index) for index in range(1, 5)]
    items[3]["platform"] = "instagram"
    rows = {
        1: _row(1),
        2: _row(2, country="DE"),
        3: _row(3, followers=100),
        4: _row(4, platform="instagram"),
    }
    evidence = {index: _video(5) for index in rows}
    selected, contract = _qualify(items, rows, evidence)
    assert [item["kol_pool_id"] for item in selected] == [1]
    assert set(contract["rejected_by_reason"]) == {
        "market_mismatch",
        "followers_below_3000",
        "platform_mismatch",
    }


# ---------------------------------------------------------------------------
# 证据腿:意图腿从 2 降到 1,产品腿一个字不动
# ---------------------------------------------------------------------------


def test_intent_leg_needs_one_proof_when_relaxed_and_two_when_strict() -> None:
    row = {"bio": "portrait photographer", "primary_topic": "", "handle": "c"}
    query = "portrait wedding studio"
    # 候选只证得出「portrait」一个意图词:AND-2 判它没有证据,松绑的 AND-1 收下它。
    assert profile_recall_match_evidence.build_match_evidence(
        row, {}, query, min_intent_terms=2
    ) == []
    relaxed = profile_recall_match_evidence.build_match_evidence(
        row, {}, query, min_intent_terms=1
    )
    assert [entry["term"] for entry in relaxed] == ["portrait"]


def test_product_leg_is_untouched_by_the_relaxation() -> None:
    """产品腿(型号 / 品牌 / 卡口 / 画幅)不在松绑范围内:证不出产品就还是没有证据。"""

    row = {"bio": "portrait photographer shooting weddings", "handle": "c"}
    assert profile_recall_match_evidence.build_match_evidence(
        row,
        {},
        "portrait photographer",
        required_product_terms=["viltrox", "af", "35mm"],
        min_intent_terms=1,
    ) == []


# ---------------------------------------------------------------------------
# 端到端:同事已关注的人不再整片消失,门面先说找到几个人
# ---------------------------------------------------------------------------


def _install_recall(
    monkeypatch: pytest.MonkeyPatch,
    rows: dict[int, dict[str, Any]],
    favorited: set[int],
) -> None:
    monkeypatch.setenv("RECALL_LLM_RERANK_ENABLED", "0")
    monkeypatch.setattr(
        profile_recall,
        "resolve_query_text",
        lambda **_kwargs: ("portrait lens review", {"query_profile": "", "query_text_provided": True}),
    )
    monkeypatch.setattr(
        profile_recall,
        "_pool_text_fallback_hits",
        lambda *_args, **_kwargs: [
            profile_recall.RecallHit(item_id, 1.0 - item_id / 1_000, f"point-{item_id}")
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
        lambda ids: {item_id: _video(5) for item_id in ids if item_id in rows},
    )
    monkeypatch.setattr(profile_recall, "_pool_rows_fallback", lambda _ids: {})
    monkeypatch.setattr(profile_recall, "_adoption_profile", lambda: {})

    def _exclude(hits: Any, **_kwargs: Any) -> tuple[list[Any], dict[str, Any]]:
        kept = [hit for hit in hits if hit.kol_pool_id not in favorited]
        excluded = sorted(hit.kol_pool_id for hit in hits if hit.kol_pool_id in favorited)
        return kept, recall_favorite_exclusion._diagnostics(
            considered=len(hits), excluded=excluded
        )

    monkeypatch.setattr(recall_favorite_exclusion, "exclude_favorited_hits", _exclude)


def _pool_row(item_id: int) -> dict[str, Any]:
    return {
        "kol_pool_id": item_id,
        "handle": f"creator-{item_id}",
        "display_name": f"Creator {item_id}",
        "platform": "youtube",
        "profile_url": f"https://example.test/{item_id}",
        "followers": 20_000 + item_id,
        "country": "US",
        "language": "en",
        "profile_type": "creator",
        "creator_type_score": 90,
        "reviewer_type_score": 10,
        "bio": "Portrait photographer publishing lens review videos",
        "primary_topic": "portrait lens review",
        "raw_platform_data": {},
    }


def _recall(gate_mode: str = "relaxed", **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "query_text": "portrait lens review",
        "provider_free": True,
        "candidate_limit": 12,
        "limit": 30,
        "creator_quota": 15,
        "reviewer_quota": 15,
        "local_qualification_policy": qualification.smart_local_policy(
            market="US", platforms=["youtube"], gate_mode=gate_mode
        ),
    }
    kwargs.update(overrides)
    return profile_recall.recall_kol_profiles(**kwargs)


def test_team_favorites_are_shown_with_a_label_instead_of_hidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = {item_id: _pool_row(item_id) for item_id in range(1, 9)}
    favorited = {2, 4, 6}
    _install_recall(monkeypatch, rows, favorited)

    result = _recall()
    returned = {item["kol_pool_id"] for item in result["items"]}
    assert favorited <= returned, "同事关注过的人不该整片消失"

    labelled = [item for item in result["items"] if item.get("team_favorite")]
    assert {item["kol_pool_id"] for item in labelled} == favorited
    for item in labelled:
        assert item["team_favorite_note"] == "已被同事关注"
        assert "已被同事关注" in item["selection_notes"]

    diagnostics = result["diagnostics"]
    assert diagnostics["favorite_exclusion"]["mode"] == "annotated"
    assert diagnostics["favorite_excluded_count"] == 0
    assert diagnostics["favorite_annotated_count"] == len(favorited)
    # 诚实注脚:没藏人就不许说「已排除」,但也要说清这些人是标注进来的。
    note = diagnostics["favorite_exclusion_note"]
    assert "已排除" not in note
    assert "已被同事关注" in note and "未从结果里隐藏" in note
    assert result["diagnostics"]["result_explanation"]["favorited_by_team_shown"] == 3
    assert result["diagnostics"]["result_explanation"]["favorited_by_team_hidden"] == 0


def test_strict_switch_puts_the_favorites_back_behind_the_wall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = {item_id: _pool_row(item_id) for item_id in range(1, 9)}
    favorited = {2, 4, 6}
    _install_recall(monkeypatch, rows, favorited)

    result = _recall(gate_mode="strict")
    diagnostics = result["diagnostics"]
    assert diagnostics["favorite_exclusion"]["mode"] == "hidden"
    assert diagnostics["favorite_excluded_count"] == len(favorited)
    assert diagnostics["favorite_annotated_count"] == 0
    assert diagnostics["search_relaxation"]["mode"] == "strict"
    # 严口径下他们只能靠回填梯的第一级回来,并且带回填章、不计入目标。
    for item in result["items"]:
        assert item["kol_pool_id"] not in favorited
    for item in result["backfill_items"]:
        if item["kol_pool_id"] in favorited:
            assert item["precision_match"] is False
            assert item["counts_toward_target"] is False


def test_headline_leads_with_how_many_people_were_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「精准命中 0 人」开头的标题,即使卡面站着 30 个人也读成「没搜到」。"""

    rows = {item_id: _pool_row(item_id) for item_id in range(1, 9)}
    _install_recall(monkeypatch, rows, set())

    explanation = _recall()["diagnostics"]["result_explanation"]
    assert explanation["returned_count"] > 0
    assert explanation["headline"].startswith("为你找到") or explanation[
        "headline"
    ].startswith("精准命中")
    assert not explanation["headline"].startswith("精准命中 0 人")

    assert ladder._headline(30, 0, 12, 0) == "为你找到 12 人(均已标注入选原因,暂无精准命中)"
    assert ladder._headline(30, 4, 6, 2) == "为你找到 12 人:精准命中 4 人,另 8 人已标注入选原因"
    assert ladder._headline(30, 30, 0, 0) == "精准命中 30 人"
    assert ladder._headline(30, 5, 0, 0) == "为你找到 5 人(均为精准命中)"


def test_deferred_rows_are_never_counted_as_precise_hits() -> None:
    """「资料待核验」的人算进总数,但绝不并进「精准命中 N 人」。"""

    explanation = ladder.explain_result(
        requested=30, precise_count=2, backfill_by_tier={}, gaps={}, deferred_count=3
    )
    assert explanation["precise_count"] == 2
    assert explanation["deferred_count"] == 3
    assert explanation["returned_count"] == 5
    assert explanation["headline"] == "为你找到 5 人:精准命中 2 人,另 3 人已标注入选原因"


def test_empty_result_still_says_it_found_nobody(monkeypatch: pytest.MonkeyPatch) -> None:
    """诚实空态不许删:真的一个人都没有时,标题不许假装有人。"""

    _install_recall(monkeypatch, {}, set())
    explanation = _recall()["diagnostics"]["result_explanation"]
    assert explanation["returned_count"] == 0
    assert explanation["headline"] == "本次没有找到符合全部条件的人选"


def test_facade_text_carries_no_internal_jargon(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = {item_id: _pool_row(item_id) for item_id in range(1, 9)}
    _install_recall(monkeypatch, rows, {2, 4})

    diagnostics = _recall()["diagnostics"]
    explanation = diagnostics["result_explanation"]
    blob = " ".join(
        [
            explanation["headline"],
            explanation["note"],
            diagnostics["favorite_exclusion_note"],
            relax.TEAM_FAVORITE_NOTE,
            relax.STALE_ACTIVITY_NOTE,
            *(entry["label"] for entry in explanation["backfill_reasons"]),
            *(entry["label"] for entry in explanation["gaps"]),
        ]
    ).lower()
    for banned in (
        "llm",
        "embedding",
        "qdrant",
        "apify",
        "payload",
        "scope",
        "rule_v0",
        "词表",
        "lexicon",
    ):
        assert banned not in blob
