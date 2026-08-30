"""证据埋点车道 · 记录构建与落库(2026-08-27)。

姊妹篇 ``test_discovery_term_evidence.py`` 守「配额记账 + 候选带着检索词溯源标」,
本文件守四样证据**怎么组装成记录、怎么活着到库里**:
  ① 本次实际用了哪几条检索词;② 产品锚是什么、来自哪条路径;
  ③ 相关性判定时手里有几个字段;④ 每条词各自产出几个合格新人、烧掉多少配额。

**单元测试绿不算完成**,所以这里有两条端到端:真的跑一遍 pipeline 的在线严格腿与
旧发现腿,断言诊断落库那一次拿到的 patch 里四样齐全。

绝对红线:本文件不碰任何质量口径(新鲜度天数 / required_terms / 器材证据 / 粉丝下限 /
检测器阈值),零触 viltrox_fit_score / rule_v0,零新增 Apify 调用。
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.domains.kol import profile_discovery_evidence as ev

# 与姊妹篇同一条长 query:锚抽取与逐词归因都按它断言,两边必须一致。
_LONG_QUERY = "young creators shooting portraits with the Viltrox AF 135mm f1.8 LAB Sony"


# ── 埋点四样:轮次观测 / 锚 / 字段普查 / 逐词归因 ─────────────────────────────
def _leg(
    platform: str, terms: list[str], *, quota: int = 0, actor: str = "",
    combined_units: int | None = None, **extra: Any,
) -> dict[str, Any]:
    searches = max(0, (quota - 1) // 100) if quota else 0
    combined = (2 if quota and platform == "youtube" else 0) if combined_units is None else combined_units
    return {
        "platform": platform,
        "status": "done",
        "metadata": {
            "actor_id": actor or "youtube-data-api/search.list:video",
            "provider_queries": terms,
            **({
                "youtube_search_calls": searches,
                "youtube_combined_quota_units": combined,
                "youtube_api_calls": searches + combined,
                "quota_units": combined,
                "quota_units_deprecated": True,
            } if quota else {}),
            **extra,
        },
    }


def _candidate(term: str, index: int, *, platform: str = "youtube", **extra: Any) -> dict[str, Any]:
    return {
        "platform": platform,
        "handle": f"{platform}-creator{index}",
        "channel_id": f"UC{index:022d}" if platform == "youtube" else "",
        "channel_url": f"https://www.youtube.com/channel/UC{index:022d}",
        "display_name": f"Creator {index}",
        "sample_title": f"{term} hands on",
        "followers": 50_000,
        ev.CANDIDATE_TERM_KEY: term,
        **extra,
    }


def test_observe_round_records_terms_quota_and_per_term_candidates() -> None:
    observation = ev.observe_round(
        round_no=1,
        platform_results=[
            _leg("youtube", ["Viltrox lens review", "young creators shooting"], quota=201),
            _leg("instagram", ["viltroxlens"], actor="apify/instagram-hashtag-scraper",
                 profile_enrich_requested=6),
        ],
        candidates=[
            _candidate("Viltrox lens review", 1),
            _candidate("Viltrox lens review", 2),
            _candidate("young creators shooting", 3),
            {"platform": "instagram", "handle": "ig1", "bio": "photographer"},
        ],
    )
    youtube = next(leg for leg in observation["legs"] if leg["platform"] == "youtube")
    assert youtube["terms"] == ["Viltrox lens review", "young creators shooting"]
    assert youtube["youtube_search_calls_actual"] == 2
    assert youtube["youtube_combined_quota_units_actual"] == 2
    assert youtube["youtube_api_calls_actual"] == 4
    assert youtube["quota_units_actual"] == 2
    assert youtube["quota_overhead_units"] == 2
    assert youtube["apify_actor_runs"] == 0            # Data API 快路径零 Apify run
    assert youtube["candidates_by_term"] == {"Viltrox lens review": 2, "young creators shooting": 1}
    assert youtube["attribution"] == "per_item"
    instagram = next(leg for leg in observation["legs"] if leg["platform"] == "instagram")
    assert instagram["quota_units_actual"] == 0        # IG 不吃 YouTube 配额
    assert instagram["apify_actor_runs"] == 2          # hashtag + profile 富化
    assert instagram["attribution"] == "shared_round"  # actor 不下发逐条溯源,如实标
    assert observation["youtube_search_calls_actual"] == 2
    assert observation["youtube_combined_quota_units_actual"] == 2
    assert observation["youtube_api_calls_actual"] == 4
    assert observation["candidates_returned"] == 4


def test_provider_term_ledger_is_the_anchor_authority_when_present() -> None:
    """检索词车道在 provider metadata 里发逐词台账 —— 埋点搬运它,不另判一套锚。

    锚是「发词那一刻」才知道的事(词梯自己知道这条词的锚来自 SKU 还是回落 5 词块),
    埋点侧再用词形猜一遍只会造出第二套互相打架的真相。
    """
    leg = _leg("youtube", ["Viltrox 135mm review", "camera gear people"], quota=201)
    leg["metadata"]["term_ledger"] = [
        {"term": "Viltrox 135mm review", "anchor": "Viltrox 135mm",
         "anchor_source": "resolved_sku", "quota_units": 100, "channels_new": 4, "exhausted": False},
        {"term": "camera gear people", "anchor": "",
         "anchor_source": "unanchored_legacy_chunk", "quota_units": 100,
         "channels_new": 1, "exhausted": True},
    ]
    observation = ev.observe_round(
        round_no=1, platform_results=[leg],
        candidates=[_candidate("Viltrox 135mm review", 1)],
    )
    assert observation["legs"][0]["term_ledger"][0]["anchor_source"] == "resolved_sku"
    record = ev.build_term_evidence(
        lane="online_strict",
        # 锚词表刻意留空:有台账时锚判定必须来自台账,而不是这里的词形比对。
        anchor={"anchor_terms": []},
        rounds=[observation],
        observed_candidates=[_candidate("Viltrox 135mm review", 1)],
        accepted_items=[],
    )
    by_term = {row["term"]: row for row in record["terms"]}
    assert by_term["Viltrox 135mm review"]["anchored"] is True
    assert by_term["Viltrox 135mm review"]["anchor_source"] == "resolved_sku"
    assert by_term["camera gear people"]["anchored"] is False
    assert by_term["camera gear people"]["exhausted"] is True
    assert record["unanchored_terms"] == ["camera gear people"]
    assert record["quota"]["unanchored_units"] == 100


def test_field_census_counts_what_was_in_hand_at_judgment_time() -> None:
    census = ev.field_census([
        {"handle": "a", "display_name": "A", "bio": "lens reviewer", "sample_title": "t",
         "followers": 1000},
        {"handle": "b"},                       # 只有 1 个字段
        {"handle": "c", "followers": 0},       # 0 粉丝 = 未知,不算证据
    ])
    assert census["candidates"] == 3
    assert census["by_field"]["handle"] == 3
    assert census["by_field"]["followers"] == 1
    assert census["by_field"]["primary_topic"] == 0     # 在线腿常年拿不到的那批
    assert census["fields_present_histogram"] == {"1": 2, "5": 1}
    assert census["fields_present_avg"] == pytest.approx(2.33, abs=0.01)
    # 诚实空态:一个候选都没有也给全形状,读端不用兼容两种结构。
    empty = ev.field_census([])
    assert empty["candidates"] == 0 and empty["fields_present_avg"] == 0.0
    assert set(empty["by_field"]) == set(census["by_field"])


def test_product_anchor_records_what_the_anchor_is_and_where_it_came_from() -> None:
    payload = {
        "product_sku": "VTX-AF-135-LAB",
        "llm_query_plan": {
            "resolved_product": {
                "sku": "VTX-AF-135-LAB", "model_name": "AF 135mm F1.8 LAB",
                "series": "LAB", "category_main": "lens",
            },
            "provider": "product_persona_kb", "model": "product_persona_kb",
            "reason": "product_persona_kb", "fallback_used": False,
        },
        "query_plan_source": "llm_plan",
    }
    anchor = ev.product_anchor_record(
        payload=payload,
        operator_anchor={"operator_product_sku": "VTX-AF-135-LAB", "operator_query": "135 e 卡口年轻用户"},
        effective_query=_LONG_QUERY,
    )
    assert anchor["kind"] == "sku"
    assert anchor["source"] == "operator_selected_sku"      # 操作员点的,不是模型猜的
    assert anchor["plan_source"] == "llm_plan"
    assert "viltrox" in " ".join(anchor["anchor_terms"]).lower() or anchor["anchor_terms"]
    # 无锚路径老实标 none/unanchored,绝不编一个锚出来。
    bare = ev.product_anchor_record(
        payload={}, operator_anchor={"operator_query": "camera people"}, effective_query="camera people",
    )
    assert bare["kind"] == "none" and bare["source"] == "unanchored"


def test_generic_term_is_named_not_hidden() -> None:
    """泛词那条必须点名,并且它烧掉的配额单独可查 —— 这是本车道存在的理由。"""
    anchor = {"anchor_terms": ["viltrox", "135mm", "lab"]}
    assert ev.term_is_anchored("the Viltrox AF 135mm f1.8", anchor["anchor_terms"]) is True
    assert ev.term_is_anchored("young creators shooting portraits with", anchor["anchor_terms"]) is False
    # 锚词表为空 = 没锚可带,不许自称带了。
    assert ev.term_is_anchored("anything at all", []) is False


def test_build_term_evidence_attributes_qualified_newcomers_per_term() -> None:
    anchor = ev.product_anchor_record(
        payload={"llm_query_plan": {"resolved_product": {
            "sku": "VTX-AF-135-LAB", "model_name": "AF 135mm F1.8 LAB", "series": "LAB",
        }}},
        operator_anchor={"operator_query": "135mm lab"},
        effective_query=_LONG_QUERY,
    )
    anchored_term = "the Viltrox AF 135mm f1.8"
    generic_term = "young creators shooting portraits with"
    candidates = [
        _candidate(anchored_term, 1),
        _candidate(anchored_term, 2),
        _candidate(generic_term, 3),
    ]
    observation = ev.observe_round(
        round_no=1,
        platform_results=[_leg("youtube", [anchored_term, generic_term], quota=201)],
        candidates=candidates,
    )
    # 合格新人 = 通过在线严格闸的那批(这里只有前两个人过闸)。
    accepted = [
        {"platform": "youtube", "handle": candidates[0]["handle"],
         "channel_url": candidates[0]["channel_url"]},
        {"platform": "youtube", "handle": candidates[1]["handle"],
         "channel_url": candidates[1]["channel_url"]},
    ]
    record = ev.build_term_evidence(
        lane="online_strict", anchor=anchor, rounds=[observation],
        observed_candidates=candidates, accepted_items=accepted,
        youtube_search_calls_forecast=3,
        youtube_combined_quota_units_forecast=2,
        youtube_api_calls_forecast=5,
    )
    assert record["schema"] == ev.TERM_EVIDENCE_SCHEMA
    by_term = {row["term"]: row for row in record["terms"]}
    assert set(by_term) == {anchored_term, generic_term}
    assert by_term[anchored_term]["qualified_new"] == 2
    assert by_term[anchored_term]["anchored"] is True
    assert by_term[anchored_term]["quota_units"] == 100
    assert by_term[generic_term]["qualified_new"] == 0
    assert by_term[generic_term]["anchored"] is False
    assert record["unanchored_terms"] == [generic_term]
    # 泛词烧掉多少配额 —— 此前只能翻日志反查,现在是个真数。
    assert record["quota"]["unanchored_units"] == 100
    assert record["quota"]["youtube_search_calls_actual"] == 2
    assert record["quota"]["youtube_combined_quota_units_actual"] == 2
    assert record["quota"]["youtube_api_calls_actual"] == 4
    assert record["quota"]["youtube_units_actual"] == 2
    assert record["quota"]["youtube_units_forecast"] == 2
    assert record["quota"]["forecast_delta_units"] == 0
    assert record["qualified_new_total"] == 2
    assert record["qualified_unattributed_count"] == 0
    assert record["field_census"]["candidates"] == 3


def test_unattributable_qualified_rows_are_counted_not_smeared() -> None:
    """连不上身份的合格新人老实记「未归因」,绝不按词平摊冒充精确。"""
    candidates = [_candidate("Viltrox lens review", 1)]
    observation = ev.observe_round(
        round_no=1,
        platform_results=[_leg("youtube", ["Viltrox lens review"], quota=101)],
        candidates=candidates,
    )
    record = ev.build_term_evidence(
        lane="online_strict", anchor={"anchor_terms": ["viltrox"]}, rounds=[observation],
        observed_candidates=candidates,
        accepted_items=[{"platform": "youtube", "handle": "someone-else-entirely"}],
    )
    assert record["qualified_unattributed_count"] == 1
    assert record["terms"][0]["qualified_new"] == 0
    assert record["quota"]["youtube_units_forecast"] is None   # 没预报就是没预报


def test_shared_round_legs_never_fake_per_term_counts() -> None:
    """IG/TT 一个 run 吃多条 query 混合返回 → 逐词产出如实写 None,不平摊。"""
    observation = ev.observe_round(
        round_no=1,
        platform_results=[_leg("tiktok", ["viltrox lens", "135mm portrait"],
                               actor="clockworks/free-tiktok-scraper")],
        candidates=[{"platform": "tiktok", "handle": "tt1"}, {"platform": "tiktok", "handle": "tt2"}],
    )
    record = ev.build_term_evidence(
        lane="online_strict", anchor={"anchor_terms": ["viltrox"]}, rounds=[observation],
        observed_candidates=[], accepted_items=[],
    )
    assert {row["attribution"] for row in record["terms"]} == {"shared_round"}
    assert all(row["candidates_returned"] is None for row in record["terms"])
    assert record["quota"]["apify_actor_runs_actual"] == 1


def test_evidence_record_survives_the_session_payload_sanitizer() -> None:
    """落库前要过 result_summary 的脱敏投影 —— 检索词不能在那一步被悄悄抹掉。"""
    from app.domains.kol.search_sessions_serde import _sanitize_session_payload

    term = "Viltrox AF 135mm f1.8 LAB review"
    candidates = [_candidate(term, 1)]
    record = ev.build_term_evidence(
        lane="online_strict",
        anchor=ev.product_anchor_record(
            payload={}, operator_anchor={"operator_query": term}, effective_query=term,
        ),
        rounds=[ev.observe_round(
            round_no=1, platform_results=[_leg("youtube", [term], quota=101)], candidates=candidates,
        )],
        observed_candidates=candidates,
        accepted_items=[{"platform": "youtube", "handle": candidates[0]["handle"]}],
    )
    survived = _sanitize_session_payload({ev.TERM_EVIDENCE_KEY: record})[ev.TERM_EVIDENCE_KEY]
    assert survived["terms"][0]["term"] == term
    assert survived["terms"][0]["qualified_new"] == 1
    assert survived["quota"]["youtube_units_actual"] == 2
    assert survived["field_census"]["candidates"] == 1


# ── 端到端:生产路径真的调用了埋点,四样真的进了那一次落库 ────────────────────
def test_strict_online_pipeline_persists_all_four_evidence_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单测绿不算数:必须证明**生产管线**那一次诊断落库里四样齐全、账目对得上。"""
    from app.domains.kol import profile_discovery_pipeline as pipeline

    recorded: list[dict[str, Any]] = []
    anchored_term = "the Viltrox AF 135mm f1.8"
    generic_term = "young creators shooting portraits with"
    candidates = [
        _candidate(anchored_term, 1),
        _candidate(anchored_term, 2),
        _candidate(generic_term, 3),
    ]

    monkeypatch.setattr(
        pipeline.profile_recall, "recall_kol_profiles",
        lambda **_kwargs: {
            "method": "test", "items": [], "buckets": {"creator": [], "reviewer": []},
            "diagnostics": {"returned_count": 0}, "local_qualification": None,
        },
    )
    monkeypatch.setattr(pipeline, "filter_recall_result_platforms", lambda result, _v: result)
    monkeypatch.setattr(pipeline, "filter_recall_result_market", lambda result, _v: result)
    monkeypatch.setattr(pipeline.search_sessions, "attach_recall_result", lambda _sid, _r: {"id": 91})
    monkeypatch.setattr(
        pipeline.search_sessions, "attach_online_qualified_result", lambda _sid, _r: {"id": 91},
    )
    monkeypatch.setattr(pipeline.search_sessions, "update_session_result_summary", lambda *_a, **_k: {})
    monkeypatch.setattr(pipeline, "_profile_advance_pipeline_status", lambda *_a: "partial")
    monkeypatch.setattr(
        pipeline, "advance_search_session_items",
        lambda **_kwargs: {
            "status": "empty", "selected": 0, "counts": {}, "items": [],
            "viltrox_fit_score_changed_ids": [],
        },
    )
    monkeypatch.setattr(
        pipeline.recall_favorite_exclusion, "favorited_identity_keys", lambda **_k: set(),
    )
    monkeypatch.setattr(
        pipeline.search_session_diagnostics, "record_search_diagnostics",
        lambda session_id, patch: recorded.append({"session_id": session_id, **patch})
        or {"status": "recorded"},
    )

    async def _fake_discover(**_kwargs: Any) -> dict[str, Any]:
        return {
            "status": "ready",
            "items": list(candidates),
            "new_creators": [dict(row) for row in candidates],
            "existing_matches": [],
            "counts": {"new_creators": 3, "existing_matches": 0},
            "platform_results": [_leg("youtube", [anchored_term, generic_term], quota=201)],
            "platforms": ["youtube"],
            "next_cursor": {"page_cursors": {}, "has_more": {}, "supported": {"youtube": True}},
            "has_more": False,
        }

    async def _fake_collect(**kwargs: Any) -> dict[str, Any]:
        await kwargs["fetch_batch"](round_no=1, limit=150, cursor=None)
        accepted = [
            {"platform": "youtube", "handle": row["handle"], "channel_url": row["channel_url"]}
            for row in candidates[:2]
        ]
        return {
            "status": "shortfall", "items": accepted, "evaluated_count": 3,
            "net_new_accepted_count": 2, "returned_count": 2, "rejected_count": 1,
            "shortfall": 28, "rejected_by_reason": {"market_unknown": 1},
            "provider_calls_performed": True, "provider_rounds": 1,
        }

    monkeypatch.setattr(pipeline, "discover_new_creators", _fake_discover)
    monkeypatch.setattr(
        pipeline.profile_online_qualification, "collect_strict_online_for_session", _fake_collect,
    )

    asyncio.run(pipeline.execute_smart_search_profile_advance_pipeline(
        session_id=91,
        payload={
            "query_text": _LONG_QUERY,
            "platforms": ["youtube"],
            "_worker_planned": True,
            "_smart_online_30_contract": True,
            "include_new_discovery": True,
            "include_content_fit": False,
            "include_lazy_video_backfill": False,
            "include_field_topup": False,
            "llm_query_plan": {
                "resolved_product": {
                    "sku": "VTX-AF-135-LAB", "model_name": "AF 135mm F1.8 LAB", "series": "LAB",
                },
                "provider": "product_persona_kb", "model": "product_persona_kb",
                "reason": "product_persona_kb",
            },
            "query_plan_source": "llm_plan",
        },
    ))

    assert len(recorded) == 1, "诊断落库只该发生一次"
    patch = recorded[0]
    evidence = patch[ev.TERM_EVIDENCE_KEY]

    # ① 实际用了哪几条检索词(不是预报常量)。
    terms = {row["term"]: row for row in evidence["terms"]}
    assert set(terms) == {anchored_term, generic_term}
    # ② 产品锚是什么、来自哪条路径。
    assert evidence["product_anchor"]["kind"] == "sku"
    assert evidence["product_anchor"]["sku"] == "VTX-AF-135-LAB"
    assert evidence["product_anchor"]["plan_source"] == "llm_plan"
    assert evidence["product_anchor"]["anchor_terms"]
    # ③ 判定时手里有几个字段。
    assert evidence["field_census"]["candidates"] == 3
    assert evidence["field_census"]["fields_checked_count"] == len(ev.JUDGMENT_FIELDS)
    assert evidence["field_census"]["by_field"]["primary_topic"] == 0
    # ④ 每条词的产出与配额,且配额与真实调用次数对得上(2 条词 × 100 + 1)。
    assert terms[anchored_term]["qualified_new"] == 2
    assert terms[generic_term]["qualified_new"] == 0
    assert terms[anchored_term]["quota_units"] == terms[generic_term]["quota_units"] == 100
    assert evidence["quota"]["youtube_search_calls_actual"] == 2
    assert evidence["quota"]["youtube_combined_quota_units_actual"] == 2
    assert evidence["quota"]["youtube_api_calls_actual"] == 4
    assert evidence["unanchored_terms"] == [generic_term]
    assert evidence["quota"]["unanchored_units"] == 100
    assert evidence["qualified_unattributed_count"] == 0

    # 轮次账本同时被对账:预报(按真实变体数)与实际并列可查。
    plan = patch["discovery_round_plan"]
    assert plan["youtube_combined_quota_units_actual"] == 2
    assert plan["youtube_combined_quota_units_total"] == 2
    assert plan["youtube_quota_units_deprecated"] is True
    assert plan["apify_runs_actual"] == 0
    # 既有漏斗留痕不受影响(本车道只加不减)。
    assert patch[pipeline.search_session_diagnostics.DISCOVERY_FUNNEL_KEY]["lane"] == "online_strict"


def test_legacy_discovery_lane_records_the_same_four_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧发现腿(非严格在线)也必须留同一份证据 —— 两条腿口径不许分叉。

    这条腿没有轮次预报表,所以顺带钉住:预报缺席时写 None,而不是拿 0 冒充「预报过 0」。
    """
    from app.domains.kol import profile_discovery_pipeline as pipeline

    recorded: list[dict[str, Any]] = []
    term = "Viltrox lens review US"
    candidates = [_candidate(term, index) for index in (1, 2)]

    monkeypatch.setattr(
        pipeline.profile_recall, "recall_kol_profiles",
        lambda **_kwargs: {
            "method": "test", "items": [], "buckets": {"creator": [], "reviewer": []},
            "diagnostics": {"returned_count": 0}, "local_qualification": None,
        },
    )
    monkeypatch.setattr(pipeline, "filter_recall_result_platforms", lambda result, _v: result)
    monkeypatch.setattr(pipeline, "filter_recall_result_market", lambda result, _v: result)
    monkeypatch.setattr(pipeline.search_sessions, "attach_recall_result", lambda _sid, _r: {"id": 92})
    monkeypatch.setattr(
        pipeline.search_sessions, "attach_new_discovery_result", lambda _sid, _r: {"id": 92},
    )
    monkeypatch.setattr(pipeline.search_sessions, "update_session_result_summary", lambda *_a, **_k: {})
    monkeypatch.setattr(pipeline, "_profile_advance_pipeline_status", lambda *_a: "partial")
    monkeypatch.setattr(
        pipeline, "advance_search_session_items",
        lambda **_kwargs: {
            "status": "empty", "selected": 0, "counts": {}, "items": [],
            "viltrox_fit_score_changed_ids": [],
        },
    )
    monkeypatch.setattr(
        pipeline.recall_favorite_exclusion, "favorited_identity_keys", lambda **_k: set(),
    )
    monkeypatch.setattr(
        pipeline.search_session_diagnostics, "record_search_diagnostics",
        lambda session_id, patch: recorded.append({"session_id": session_id, **patch})
        or {"status": "recorded"},
    )

    async def _fake_discover(**_kwargs: Any) -> dict[str, Any]:
        return {
            "status": "ready",
            "items": list(candidates),
            "new_creators": [dict(row) for row in candidates],
            "existing_matches": [],
            "counts": {"new_creators": 2, "existing_matches": 0},
                "platform_results": [_leg("youtube", [term], quota=101, combined_units=1)],
            "platforms": ["youtube"],
        }

    monkeypatch.setattr(pipeline, "discover_new_creators", _fake_discover)

    asyncio.run(pipeline.execute_smart_search_profile_advance_pipeline(
        session_id=92,
        payload={
            "query_text": "Viltrox lens review",
            "platforms": ["youtube"],
            "_worker_planned": True,
            "include_new_discovery": True,
            "include_content_fit": False,
            "include_lazy_video_backfill": False,
            "include_field_topup": False,
        },
    ))

    assert len(recorded) == 1
    evidence = recorded[0][ev.TERM_EVIDENCE_KEY]
    assert evidence["lane"] == "legacy_discovery"
    assert [row["term"] for row in evidence["terms"]] == [term]
    assert evidence["terms"][0]["qualified_new"] == 2
    assert evidence["quota"]["youtube_units_actual"] == 1
    assert evidence["quota"]["youtube_units_forecast"] is None
    assert evidence["quota"]["forecast_delta_units"] is None
    assert evidence["field_census"]["candidates"] == 2
    # legacy 腿本就不写轮次账本,这刀不许偷偷给它加一份。
    assert "discovery_round_plan" not in recorded[0]
