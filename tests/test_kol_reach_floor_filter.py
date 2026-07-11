"""召回触达门槛过滤契约测试(用户裁决 2026-07-11)。

口径:KOL 发现/推荐候选层,followers 明确 < 门槛(默认 1000,env VKPI_DISCOVERY_MIN_FOLLOWERS
可调)或互动信号实测全零 → 不进推荐/发现列表;字段真缺/NULL/fast_path 填充 0 一律放行(不误杀);
总开关 env VKPI_DISCOVERY_REACH_FLOOR_ENABLED(默认开)。
red line:纯召回层 FILTER,零触 viltrox_fit_score / rule_v0 / 任何评分公式。

覆盖三出口接线:
① recall_kol_profiles(智能搜寻「库内已有的人」)
② discover_new_creators(「全网新发现的人」结果流)
③ build_new_launch_match_preview(推荐刷新 refresh_recommendations 的引擎)
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.domains.kol.discovery_filters import (
    _below_reach_floor,
    _country_in_excluded_region,
    _detect_excluded_region,
    _reach_floor_reason,
)


# ── 纯函数契约 ────────────────────────────────────────────────────────────────


def test_followers_below_floor_blocked() -> None:
    assert _below_reach_floor({"followers": 500}) is True
    assert _below_reach_floor({"followers": 999}) is True
    assert _below_reach_floor({"followers": 0}) is True
    assert "followers_below_floor" in _reach_floor_reason({"followers": 500})


def test_followers_at_or_above_floor_pass() -> None:
    assert _below_reach_floor({"followers": 1000}) is False
    assert _below_reach_floor({"followers": 250_000}) is False


def test_missing_or_null_followers_not_blocked() -> None:
    # 字段真缺 / NULL 读回 → 未知 → 放行(不误杀)。
    assert _below_reach_floor({"handle": "someone"}) is False
    assert _below_reach_floor({"followers": None}) is False
    assert _below_reach_floor({"followers": ""}) is False
    assert _below_reach_floor(None) is False


def test_follower_key_aliases_recognized() -> None:
    assert _below_reach_floor({"follower_count": 300}) is True
    assert _below_reach_floor({"subscriber_count": 300}) is True
    assert _below_reach_floor({"subscriber_count": 30_000}) is False


def test_all_zero_engagement_blocked() -> None:
    # 播放族+评论族都明确实测 0,且无其他正互动信号 → 挡。
    assert _below_reach_floor({"views": 0, "comments": 0, "likes": 0}) is True
    assert "no_engagement_signal" in _reach_floor_reason(
        {"avg_views": 0, "avg_comments": 0, "engagement_rate": None}
    )


def test_any_positive_engagement_passes() -> None:
    assert _below_reach_floor({"views": 1200, "comments": 0, "likes": 0}) is False
    assert _below_reach_floor({"views": 0, "comments": 3, "likes": 0}) is False
    # 播放/评论实测 0 但 likes/engagement 有正信号 → 放行。
    assert _below_reach_floor({"views": 0, "comments": 0, "likes": 88}) is False
    assert _below_reach_floor({"avg_views": 0, "avg_comments": 0, "engagement_rate": 0.031}) is False


def test_partially_missing_engagement_not_blocked() -> None:
    # 任一族字段真缺 → 未知 → 放行(不误杀)。
    assert _below_reach_floor({"views": 0}) is False
    assert _below_reach_floor({"comments": 0}) is False
    assert _below_reach_floor({"views": 0, "comments": None}) is False


def test_fast_path_zero_fill_exempt() -> None:
    # YouTube Data API search.list 无统计数据,views/comments=0 是填充非实测 → 豁免。
    assert _below_reach_floor({"views": 0, "comments": 0, "likes": 0, "fast_path": True}) is False
    # 但 fast_path 不豁免 followers 明确低于门槛。
    assert _below_reach_floor({"followers": 200, "views": 0, "comments": 0, "fast_path": True}) is True


def test_env_threshold_adjustable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VKPI_DISCOVERY_MIN_FOLLOWERS", "5000")
    assert _below_reach_floor({"followers": 3000}) is True
    assert _below_reach_floor({"followers": 6000}) is False


def test_env_threshold_bad_value_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VKPI_DISCOVERY_MIN_FOLLOWERS", "not-a-number")
    assert _below_reach_floor({"followers": 999}) is True
    assert _below_reach_floor({"followers": 1000}) is False
    monkeypatch.setenv("VKPI_DISCOVERY_MIN_FOLLOWERS", "-5")
    assert _below_reach_floor({"followers": 999}) is True


def test_switch_off_disables_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VKPI_DISCOVERY_REACH_FLOOR_ENABLED", "0")
    assert _below_reach_floor({"followers": 10}) is False
    assert _below_reach_floor({"views": 0, "comments": 0}) is False
    assert _reach_floor_reason({"followers": 10}) == ""


def test_chinese_region_exclusion_unaffected(monkeypatch: pytest.MonkeyPatch) -> None:
    # 地区排除(同族过滤)与触达门槛互相独立:门槛开/关都不影响地区判据。
    for switch in ("1", "0"):
        monkeypatch.setenv("VKPI_DISCOVERY_REACH_FLOOR_ENABLED", switch)
        assert _country_in_excluded_region("CN") is True
        assert _country_in_excluded_region("US") is False
        assert _detect_excluded_region({"sample_title": "#中国广州 街拍", "channel_name": "", "handle": ""}) == "CN/HK/TW"
        assert _detect_excluded_region({"sample_title": "street photography NYC", "channel_name": "", "handle": ""}) == ""
    # 反向:地区中立的低粉候选仍被门槛挡(过滤各司其职)。
    monkeypatch.setenv("VKPI_DISCOVERY_REACH_FLOOR_ENABLED", "1")
    assert _below_reach_floor({"followers": 500, "country": "US"}) is True


# ── 出口① 智能搜寻「库内已有的人」:recall_kol_profiles ───────────────────────


def test_recall_kol_profiles_filters_low_reach(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.kol import profile_recall

    rows = {
        1: {"kol_pool_id": 1, "handle": "tiny", "platform": "youtube", "profile_type": "creator",
            "followers": 500, "country": "US"},
        2: {"kol_pool_id": 2, "handle": "big", "platform": "youtube", "profile_type": "creator",
            "followers": 250_000, "country": "US"},
        3: {"kol_pool_id": 3, "handle": "unknown_reach", "platform": "youtube", "profile_type": "creator",
            "followers": None, "country": "US"},
        4: {"kol_pool_id": 4, "handle": "cn_big", "platform": "youtube", "profile_type": "creator",
            "followers": 250_000, "country": "CN"},
        5: {"kol_pool_id": 5, "handle": "dead", "platform": "youtube", "profile_type": "creator",
            "followers": 5000, "avg_views": 0, "avg_comments": 0, "engagement_rate": None, "country": "US"},
    }
    hits = [profile_recall.RecallHit(kol_pool_id=i, vector_score=0.9 - i * 0.01, qdrant_point_id=f"p{i}") for i in rows]

    monkeypatch.setenv("RECALL_LLM_RERANK_ENABLED", "0")
    monkeypatch.setattr(profile_recall, "resolve_query_text", lambda **_kw: ("test query", {"query_profile": ""}))
    monkeypatch.setattr(profile_recall, "_embed_query", lambda text: ([0.1, 0.2], {}))
    monkeypatch.setattr(profile_recall, "_search_qdrant", lambda vector, limit: hits)
    monkeypatch.setattr(profile_recall, "_entry_rows", lambda ids: {i: dict(rows[i]) for i in ids if i in rows})
    monkeypatch.setattr(profile_recall, "_evidence_summaries", lambda ids: {})
    monkeypatch.setattr(profile_recall, "_pool_rows_fallback", lambda ids: {})
    monkeypatch.setattr(profile_recall, "_adoption_profile", lambda: {})

    result = profile_recall.recall_kol_profiles(query_text="flash photography", creator_quota=10, reviewer_quota=5, limit=20)

    returned_handles = {item["handle"] for item in result["items"]}
    assert returned_handles == {"big", "unknown_reach"}  # tiny/dead 被门槛挡,cn_big 被地区排除
    assert result["diagnostics"]["filtered_low_reach"] == 2


def test_recall_kol_profiles_switch_off_keeps_low_reach(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.kol import profile_recall

    rows = {
        1: {"kol_pool_id": 1, "handle": "tiny", "platform": "youtube", "profile_type": "creator",
            "followers": 500, "country": "US"},
    }
    hits = [profile_recall.RecallHit(kol_pool_id=1, vector_score=0.9, qdrant_point_id="p1")]

    monkeypatch.setenv("RECALL_LLM_RERANK_ENABLED", "0")
    monkeypatch.setenv("VKPI_DISCOVERY_REACH_FLOOR_ENABLED", "0")
    monkeypatch.setattr(profile_recall, "resolve_query_text", lambda **_kw: ("test query", {"query_profile": ""}))
    monkeypatch.setattr(profile_recall, "_embed_query", lambda text: ([0.1], {}))
    monkeypatch.setattr(profile_recall, "_search_qdrant", lambda vector, limit: hits)
    monkeypatch.setattr(profile_recall, "_entry_rows", lambda ids: {i: dict(rows[i]) for i in ids if i in rows})
    monkeypatch.setattr(profile_recall, "_evidence_summaries", lambda ids: {})
    monkeypatch.setattr(profile_recall, "_pool_rows_fallback", lambda ids: {})
    monkeypatch.setattr(profile_recall, "_adoption_profile", lambda: {})

    result = profile_recall.recall_kol_profiles(query_text="flash photography", creator_quota=10, reviewer_quota=5)
    assert {item["handle"] for item in result["items"]} == {"tiny"}
    assert result["diagnostics"]["filtered_low_reach"] == 0


# ── 出口② 「全网新发现的人」:discover_new_creators ──────────────────────────


def _discovery_item(handle: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "platform": "youtube",
        "channel_name": handle.title(),
        "handle": handle,
        "avatar_url": "",
        "thumbnail_url": "",
        "channel_url": f"https://www.youtube.com/@{handle}",
        "source_url": f"https://www.youtube.com/watch?v={handle}",
        "sample_title": "cinematic b-roll photography tutorial",
        "views": 5000,
        "likes": 120,
        "comments": 30,
        "avg_views": 5000,
        "published": "",
        "market": "US",
        "search_query": "flash photography",
        "provider_actor": "test",
    }
    base.update(overrides)
    return base


def test_discover_new_creators_filters_low_reach(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.kol import profile_discovery

    items = [
        _discovery_item("goodcreator"),
        _discovery_item("lowfollowers", followers=800),  # followers 明确 < 1000 → 挡
        _discovery_item("deadaccount", views=0, likes=0, comments=0, avg_views=0),  # 互动实测全零 → 挡
        _discovery_item("fastpathchannel", views=0, likes=0, comments=0, avg_views=0, fast_path=True),  # 填充 0 → 放行
    ]

    async def fake_search(platform: str, query: str, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "done", "items": [dict(item) for item in items], "metadata": {}}

    monkeypatch.setattr(profile_discovery, "search_platform_content", fake_search)
    monkeypatch.setattr(profile_discovery.history_match, "annotate_platform_items", lambda raw, platform: raw)
    monkeypatch.setattr(profile_discovery, "_auto_enroll_discoveries", lambda creators: 0)

    result = asyncio.run(
        profile_discovery.discover_new_creators(
            query_text="flash photography", platforms=["youtube"], market="US", limit=10, per_platform_limit=10
        )
    )

    handles = {item["handle"] for item in result["new_creators"]}
    assert handles == {"goodcreator", "fastpathchannel"}
    assert result["counts"]["filtered_low_reach"] == 2


def test_discover_new_creators_switch_off_keeps_all(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.kol import profile_discovery

    items = [_discovery_item("lowfollowers", followers=800)]

    async def fake_search(platform: str, query: str, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "done", "items": [dict(item) for item in items], "metadata": {}}

    monkeypatch.setenv("VKPI_DISCOVERY_REACH_FLOOR_ENABLED", "0")
    monkeypatch.setattr(profile_discovery, "search_platform_content", fake_search)
    monkeypatch.setattr(profile_discovery.history_match, "annotate_platform_items", lambda raw, platform: raw)
    monkeypatch.setattr(profile_discovery, "_auto_enroll_discoveries", lambda creators: 0)

    result = asyncio.run(
        profile_discovery.discover_new_creators(
            query_text="flash photography", platforms=["youtube"], market="US", limit=10, per_platform_limit=10
        )
    )
    assert {item["handle"] for item in result["new_creators"]} == {"lowfollowers"}
    assert result["counts"]["filtered_low_reach"] == 0


# ── 出口③ 推荐刷新引擎:build_new_launch_match_preview ────────────────────────


def _wire_new_launch_match(monkeypatch: pytest.MonkeyPatch) -> Any:
    import app.domains.recommendations.new_launch_match as nlm

    monkeypatch.setattr(
        nlm.memory, "readiness", lambda: {"status": "ready_for_p4_dry_run", "provider_calls_allowed": False}
    )
    monkeypatch.setattr(nlm, "check_budget", lambda scope, cost: True)
    monkeypatch.setattr(nlm, "get_budget_status", lambda scope, estimated_cost=0.0: {"configured": False})
    monkeypatch.setattr(
        nlm, "_select_target_family", lambda q: {"id": 1, "entity_uid": "fam-1", "display_name": "Test Family"}
    )
    monkeypatch.setattr(nlm, "_product_family_maps", lambda: ({}, {}))
    monkeypatch.setattr(
        nlm,
        "_kol_entities",
        lambda: [
            {"id": 11, "entity_uid": "kol-big", "display_name": "Big", "status": "",
             "identity_json": '{"source_ref": "ref-big"}', "metadata_json": "{}"},
            {"id": 12, "entity_uid": "kol-small", "display_name": "Small", "status": "",
             "identity_json": '{"source_ref": "ref-small"}', "metadata_json": "{}"},
            {"id": 13, "entity_uid": "kol-nopool", "display_name": "NoPool", "status": "",
             "identity_json": '{"source_ref": "ref-missing"}', "metadata_json": "{}"},
        ],
    )
    monkeypatch.setattr(
        nlm,
        "_pool_by_source_ref",
        lambda: {
            "ref-big": {"id": 101, "platform": "youtube", "handle": "big", "display_name": "Big",
                        "country": "", "sync_status": "", "followers": 250_000,
                        "avg_views": None, "avg_comments": None, "engagement_rate": None},
            "ref-small": {"id": 102, "platform": "youtube", "handle": "small", "display_name": "Small",
                          "country": "", "sync_status": "", "followers": 400,
                          "avg_views": None, "avg_comments": None, "engagement_rate": None},
        },
    )
    monkeypatch.setattr(nlm, "_legacy_entities_by_uid", lambda: {})
    monkeypatch.setattr(nlm, "_kol_facts", lambda: {})
    monkeypatch.setattr(nlm, "_worked_links", lambda: {})
    monkeypatch.setattr(nlm, "_target_market_signals", lambda fid: [])
    monkeypatch.setattr(nlm, "_market_signal_score", lambda signals, *, now: (0, []))
    return nlm


def test_new_launch_match_filters_low_reach(monkeypatch: pytest.MonkeyPatch) -> None:
    nlm = _wire_new_launch_match(monkeypatch)
    payload = nlm.build_new_launch_match_preview(product_query="Test Family", limit=10)

    uids = {item["kol_entity_uid"] for item in payload["items"]}
    # 低粉(400)被挡;pool 缺行(reach 未知)放行不误杀。
    assert uids == {"kol-big", "kol-nopool"}
    assert payload["summary"]["filtered_low_reach"] == 1


def test_new_launch_match_switch_off_keeps_low_reach(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VKPI_DISCOVERY_REACH_FLOOR_ENABLED", "0")
    nlm = _wire_new_launch_match(monkeypatch)
    payload = nlm.build_new_launch_match_preview(product_query="Test Family", limit=10)

    uids = {item["kol_entity_uid"] for item in payload["items"]}
    assert uids == {"kol-big", "kol-small", "kol-nopool"}
    assert payload["summary"]["filtered_low_reach"] == 0
