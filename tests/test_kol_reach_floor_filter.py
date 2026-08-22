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
    # 2026-07-12 裁决升级:followers 未知(NULL)也不进推荐面(「分析后再 po」)→
    # unknown_reach 折叠进 filtered_unknown_reach,不再露出。
    assert returned_handles == {"big"}  # tiny/dead 被门槛挡,cn_big 被地区排除,unknown_reach 归分析中
    assert result["diagnostics"]["filtered_low_reach"] == 2
    assert result["diagnostics"]["filtered_unknown_reach"] == 1


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
    assert result["diagnostics"]["filtered_unknown_reach"] == 0


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
        _discovery_item("bigfollowers", followers=50_000),  # followers 已知达标 → 直接可见
    ]

    async def fake_search(platform: str, query: str, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "done", "items": [dict(item) for item in items], "metadata": {}}

    enrolled_handles: list[str] = []

    def fake_enroll(creators: list[dict[str, Any]]) -> int:
        enrolled_handles.extend(str(c.get("handle")) for c in creators)
        return len(creators)

    monkeypatch.setattr(profile_discovery, "search_platform_content", fake_search)
    monkeypatch.setattr(profile_discovery.history_match, "annotate_platform_items", lambda raw, platform: raw)
    monkeypatch.setattr(profile_discovery, "_auto_enroll_discoveries", fake_enroll)

    result = asyncio.run(
        profile_discovery.discover_new_creators(
            query_text="flash photography", platforms=["youtube"], market="US", limit=10, per_platform_limit=10
        )
    )

    handles = {item["handle"] for item in result["new_creators"]}
    assert handles == {"goodcreator", "fastpathchannel", "bigfollowers"}
    assert result["counts"]["filtered_low_reach"] == 2
    # 2026-07-12 第二道闸「分析后再 po」:followers 未知 → 标 reach_status=analyzing
    # (照样入库+点火补全,由会话读端折叠为「分析中 ×N」);已知达标 → ok 直接可见。
    status_by_handle = {item["handle"]: item.get("reach_status") for item in result["new_creators"]}
    assert status_by_handle == {"goodcreator": "analyzing", "fastpathchannel": "analyzing", "bigfollowers": "ok"}
    assert result["counts"]["analyzing"] == 2
    # 分析中的候选也必须入库(发现→自动入库→补全→过闸→再 po 的链头)。
    assert set(enrolled_handles) == {"goodcreator", "fastpathchannel", "bigfollowers"}


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
    # 总开关关闭 → 三态恒 ok,无「分析中」折叠(全放行,与既有开关语义一致)。
    assert result["counts"]["analyzing"] == 0
    assert result["new_creators"][0].get("reach_status") == "ok"


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
            {"id": 14, "entity_uid": "kol-null", "display_name": "NullFollowers", "status": "",
             "identity_json": '{"source_ref": "ref-null"}', "metadata_json": "{}"},
            {"id": 15, "entity_uid": "kol-flagged", "display_name": "Flagged", "status": "",
             "identity_json": '{"source_ref": "ref-flagged"}', "metadata_json": "{}"},
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
            # followers 未知(NULL)→ 2026-07-12 裁决:不进推荐面(「分析后再 po」)。
            "ref-null": {"id": 103, "platform": "youtube", "handle": "nullf", "display_name": "NullFollowers",
                         "country": "", "sync_status": "", "followers": None,
                         "avg_views": None, "avg_comments": None, "engagement_rate": None},
            # 补全后被第二道闸打了 low_reach 标(followers 又回 NULL 的兜底场景)→ 标兜住,不进推荐面。
            "ref-flagged": {"id": 104, "platform": "youtube", "handle": "flagged", "display_name": "Flagged",
                            "country": "", "sync_status": "", "followers": 30_000,
                            "avg_views": None, "avg_comments": None, "engagement_rate": None,
                            "raw_platform_data": '{"low_reach": {"flag": true, "reason": "followers_below_floor(2<1000)"}}'},
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
    # 低粉(400)+ low_reach 标(第二道闸)被挡;followers=NULL 归「分析后再 po」不落推荐;
    # pool 缺行(legacy 无池身)放行不误杀。
    assert uids == {"kol-big", "kol-nopool"}
    assert payload["summary"]["filtered_low_reach"] == 2  # 低粉 400 + low_reach 标
    assert payload["summary"]["filtered_unknown_reach"] == 1  # followers=NULL


def test_new_launch_match_switch_off_keeps_low_reach(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VKPI_DISCOVERY_REACH_FLOOR_ENABLED", "0")
    nlm = _wire_new_launch_match(monkeypatch)
    payload = nlm.build_new_launch_match_preview(product_query="Test Family", limit=10)

    uids = {item["kol_entity_uid"] for item in payload["items"]}
    assert uids == {"kol-big", "kol-small", "kol-nopool", "kol-null", "kol-flagged"}
    assert payload["summary"]["filtered_low_reach"] == 0
    assert payload["summary"]["filtered_unknown_reach"] == 0


# ══ 第二道闸(2026-07-12 用户 live 实锤:kol_pool 12297 两粉号 NULL 旁路穿闸案)═══════════
# 发现时 followers=NULL 放行(不误杀,正确)→ 档案补全回填 followers=2 → 无第二道闸照样
# 出现在推荐面。修复:①回填后重过闸打 low_reach 标;②三出口 + 会话读端读「实时判据+标」
# 双保险;③followers 未知「分析后再 po」(折叠为分析中,不露出)。


# ── 纯函数三态 ────────────────────────────────────────────────────────────────


def test_reach_display_state_three_way() -> None:
    from app.domains.kol.discovery_filters import _reach_display_state

    assert _reach_display_state({"followers": 50_000}) == "ok"
    assert _reach_display_state({"followers": 2}) == "low_reach"  # 12297 案回填后的真值
    assert _reach_display_state({"followers": None}) == "unknown"
    assert _reach_display_state({"handle": "someone"}) == "unknown"
    assert _reach_display_state(None) == "ok"  # 非 dict 不闸(与 _reach_floor_reason 同口径)


def test_reach_display_state_reads_low_reach_flag() -> None:
    from app.domains.kol.discovery_filters import _low_reach_flagged, _reach_display_state

    # ① SQL 侧算好的 low_reach_flagged 列(BOOLEAN 读回 int 1/0 也认——truthy 容错)。
    assert _reach_display_state({"followers": 30_000, "low_reach_flagged": 1}) == "low_reach"
    assert _reach_display_state({"followers": 30_000, "low_reach_flagged": True}) == "low_reach"
    assert _reach_display_state({"followers": 30_000, "low_reach_flagged": 0}) == "ok"
    # ② 行内 raw_platform_data JSON(str/dict 双形态)。
    raw = '{"low_reach": {"flag": true, "reason": "followers_below_floor(2<1000)"}}'
    assert _low_reach_flagged({"raw_platform_data": raw}) is True
    assert _reach_display_state({"followers": 30_000, "raw_platform_data": raw}) == "low_reach"
    assert _low_reach_flagged({"raw_platform_data": '{"other": 1}', "followers": 5}) is False
    assert _low_reach_flagged({"raw_platform_data": {"low_reach": {"flag": True}}}) is True


def test_reach_display_state_switch_off_all_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.kol.discovery_filters import _reach_display_state

    monkeypatch.setenv("VKPI_DISCOVERY_REACH_FLOOR_ENABLED", "0")
    assert _reach_display_state({"followers": 2}) == "ok"
    assert _reach_display_state({"followers": None}) == "ok"
    assert _reach_display_state({"followers": 30_000, "low_reach_flagged": 1}) == "ok"


# ── 回填后重过闸(打标/摘标)────────────────────────────────────────────────────


def test_evaluate_low_reach_stamp_flags_backfilled_low_follower_row() -> None:
    import json

    from app.domains.kol.reach_floor_regate import evaluate_low_reach_stamp

    # 12297 案形态:补全回填 followers=2,raw 里已有别的键 → 打标且保留原键。
    verdict = evaluate_low_reach_stamp({
        "id": 12297, "followers": 2, "avg_views": None, "avg_comments": None,
        "raw_platform_data": '{"profile_backfill": {"method": "kol_profile_basics_safe_writer_v1"}}',
    })
    assert verdict["flagged"] is True and verdict["changed"] is True
    payload = json.loads(verdict["raw_json"])
    assert payload["low_reach"]["flag"] is True
    assert "followers_below_floor(2<1000)" in payload["low_reach"]["reason"]
    assert payload["profile_backfill"]["method"] == "kol_profile_basics_safe_writer_v1"  # 原键不丢


def test_evaluate_low_reach_stamp_unstamps_grown_row() -> None:
    import json

    from app.domains.kol.reach_floor_regate import evaluate_low_reach_stamp

    verdict = evaluate_low_reach_stamp({
        "id": 1, "followers": 80_000,
        "raw_platform_data": '{"low_reach": {"flag": true}, "keep": 1}',
    })
    assert verdict["flagged"] is False and verdict["changed"] is True  # 曾打标、现达标 → 摘标
    payload = json.loads(verdict["raw_json"])
    assert "low_reach" not in payload and payload["keep"] == 1


def test_evaluate_low_reach_stamp_no_change_for_unknown_or_clean_pass() -> None:
    from app.domains.kol.reach_floor_regate import evaluate_low_reach_stamp

    # followers 仍未知 → 不打标(留给「分析中」态);从未打过标且达标 → 零写。
    assert evaluate_low_reach_stamp({"id": 1, "followers": None, "raw_platform_data": "{}"})["changed"] is False
    assert evaluate_low_reach_stamp({"id": 2, "followers": 9_000, "raw_platform_data": "{}"})["changed"] is False


class _FakeRegateConn:
    """reapply_reach_floor 的最小 conn 假体(execute/fetchone/commit)。"""

    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row
        self.updates: list[tuple[str, tuple[Any, ...]]] = []
        self.committed = 0

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> "_FakeRegateConn":
        self._last_sql = sql
        if sql.strip().upper().startswith("UPDATE"):
            self.updates.append((sql, params))
        return self

    def fetchone(self) -> dict[str, Any] | None:
        return dict(self.row) if self.row else None

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        return None


def test_reapply_reach_floor_writes_flag_only_raw_column() -> None:
    from app.domains.kol.reach_floor_regate import reapply_reach_floor

    conn = _FakeRegateConn({
        "id": 12297, "followers": 2, "avg_views": None, "avg_comments": None,
        "engagement_rate": None, "raw_platform_data": "{}",
    })
    result = reapply_reach_floor(12297, conn=conn)
    assert result["flagged"] is True and result["changed"] is True
    assert len(conn.updates) == 1 and conn.committed == 1
    update_sql, update_params = conn.updates[0]
    # 红线:只写 raw_platform_data 一列,零触 viltrox_fit_score / 评分域。
    assert "raw_platform_data" in update_sql
    assert "viltrox_fit" not in update_sql
    assert update_params[-1] == 12297


def test_reapply_reach_floor_skips_when_pass_and_env_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.kol.reach_floor_regate import reapply_reach_floor

    conn = _FakeRegateConn({
        "id": 5, "followers": 90_000, "avg_views": None, "avg_comments": None,
        "engagement_rate": None, "raw_platform_data": "{}",
    })
    assert reapply_reach_floor(5, conn=conn)["changed"] is False
    assert not conn.updates

    monkeypatch.setenv("VKPI_DISCOVERY_REACH_FLOOR_ENABLED", "0")
    low = _FakeRegateConn({
        "id": 6, "followers": 2, "avg_views": None, "avg_comments": None,
        "engagement_rate": None, "raw_platform_data": "{}",
    })
    result = reapply_reach_floor(6, conn=low)
    assert result.get("skipped") == "env_off" and not low.updates  # 关闸不打标也不摘标


# ── 会话读端展示闸(前端「全网新发现/库内已有/召回」消费面)───────────────────────


class _FakeGateConn:
    """_apply_reach_display_gate 的最小 conn 假体:按 SQL 形状路由 id 直查/平台+handle 反查。"""

    def __init__(self, rows: list[dict[str, Any]], fail: bool = False) -> None:
        self.rows = rows
        self.fail = fail

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> "_FakeGateConn":
        if self.fail:
            raise RuntimeError("db down")
        self._sql = sql
        self._params = params
        return self

    def fetchall(self) -> list[dict[str, Any]]:
        if "WHERE id IN" in self._sql:
            wanted = {int(p) for p in self._params[1:]}
            return [dict(r) for r in self.rows if int(r["id"]) in wanted]
        pairs = set()
        raw = list(self._params[1:])
        for i in range(0, len(raw), 2):
            pairs.add((str(raw[i]).lower(), str(raw[i + 1]).lower()))
        return [
            dict(r) for r in self.rows
            if (str(r.get("platform")).lower(), str(r.get("handle")).lower()) in pairs
        ]


def _session_item(item_type: str, *, kol_pool_id: int | None = None, platform: str = "youtube",
                  handle: str = "", **payload: Any) -> dict[str, Any]:
    return {
        "item_type": item_type,
        "kol_pool_id": kol_pool_id,
        "payload": {"platform": platform, "handle": handle, **payload},
    }


def test_session_reach_display_gate_hides_backfilled_low_reach_and_analyzing() -> None:
    from app.domains.kol.search_sessions import _apply_reach_display_gate

    pool_rows = [
        # 12297 案:new_creator 会话项 kol_pool_id 恒 NULL → 按 (platform, handle) 反查现值。
        {"id": 12297, "platform": "youtube", "handle": "UCqfGG_OEwUbeaYImjOGblFw",
         "followers": 2, "avg_views": None, "avg_comments": None, "engagement_rate": None,
         "low_reach_flagged": 1},
        {"id": 300, "platform": "youtube", "handle": "bigshot",
         "followers": 120_000, "avg_views": None, "avg_comments": None, "engagement_rate": None,
         "low_reach_flagged": 0},
        {"id": 301, "platform": "youtube", "handle": "stillnull",
         "followers": None, "avg_views": None, "avg_comments": None, "engagement_rate": None,
         "low_reach_flagged": 0},
    ]
    items = [
        _session_item("new_creator", handle="UCqfGG_OEwUbeaYImjOGblFw", views=5000),  # 回填 2 粉 → 隐
        _session_item("new_creator", handle="bigshot", views=9000),                    # 回填达标 → 显
        _session_item("existing_kol", kol_pool_id=301, handle="stillnull"),            # 仍 NULL → 分析中
        _session_item("recall_candidate", kol_pool_id=300, handle="bigshot"),          # 达标 → 显
        _session_item("url_video", handle="whatever"),                                 # 用户点名分析 → 不闸
    ]
    visible, counts = _apply_reach_display_gate(_FakeGateConn(pool_rows), items)

    visible_keys = [(it["item_type"], it["payload"].get("handle")) for it in visible]
    assert ("new_creator", "UCqfGG_OEwUbeaYImjOGblFw") not in visible_keys  # 12297 不再出现在全网新发现
    assert ("new_creator", "bigshot") in visible_keys
    assert ("recall_candidate", "bigshot") in visible_keys
    assert ("url_video", "whatever") in visible_keys
    # 2026-08-22 裁决:发现面(existing_kol/new_creator)followers 未知照常上墙、标 analyzing,
    # 不再藏成「分析中 ×N」;计入 visible_analyzing。
    assert ("existing_kol", "stillnull") in visible_keys
    stillnull = next(it for it in visible if it["payload"].get("handle") == "stillnull")
    assert stillnull["payload"]["reach_status"] == "analyzing"
    bigshot_new = next(it for it in visible if it["item_type"] == "new_creator" and it["payload"].get("handle") == "bigshot")
    assert bigshot_new["payload"]["reach_status"] == "ok"
    assert bigshot_new["payload"]["followers"] == 120_000  # pool 现值补进快照(读端投影)
    assert counts["hidden_low_reach"] == 1
    assert counts["hidden_analyzing"] == 0
    assert counts["visible_analyzing"] == 1
    assert counts["by_type"]["new_creator"]["hidden_low_reach"] == 1
    assert counts["by_type"]["existing_kol"]["visible_analyzing"] == 1


def test_session_reach_display_gate_pool_missing_falls_back_to_payload() -> None:
    from app.domains.kol.search_sessions import _apply_reach_display_gate

    items = [
        # 池行缺 + payload followers 已知低 → 实时判据兜底挡掉。
        _session_item("new_creator", handle="ghost_low", followers=120),
        # 池行缺 + payload followers 未知 → 归分析中(诚实折叠,不冒充达标)。
        _session_item("new_creator", handle="ghost_unknown", views=8000),
    ]
    visible, counts = _apply_reach_display_gate(_FakeGateConn([]), items)
    assert [it["payload"]["handle"] for it in visible] == ["ghost_unknown"]
    assert visible[0]["payload"]["reach_status"] == "analyzing"
    assert "followers" not in visible[0]["payload"]  # 未知不编数字,前端显示「粉丝数待核」
    assert counts["hidden_low_reach"] == 1 and counts["hidden_analyzing"] == 0 and counts["visible_analyzing"] == 1


def test_session_reach_display_gate_recall_candidate_unknown_still_folds() -> None:
    """推荐面(框2 库内召回)口径不变:followers 未知仍折叠为「分析中」。"""
    from app.domains.kol.search_sessions import _apply_reach_display_gate

    items = [_session_item("recall_candidate", kol_pool_id=900, handle="quiet")]
    pool_rows = [{"id": 900, "platform": "youtube", "handle": "quiet", "followers": None,
                  "avg_views": None, "avg_comments": None, "engagement_rate": None, "low_reach_flagged": 0}]
    visible, counts = _apply_reach_display_gate(_FakeGateConn(pool_rows), items)
    assert visible == []
    assert counts["hidden_analyzing"] == 1 and counts["visible_analyzing"] == 0


def test_session_reach_display_gate_youtube_pool_row_found_by_channel_id() -> None:
    """会话 1106 案:YT 池行 handle=UC 频道 id、会话项 handle=@customUrl → 旧单键查不到,
    回落 payload(search.list 填充 views/comments=0,且快照没带 fast_path/followers)误判低触达。
    现按 channel_id / channel_url 里的 UC id 作第二键反查池行现值(7020 粉)→ 正常上墙。"""
    from app.domains.kol.search_sessions import _apply_reach_display_gate

    pool_rows = [{"id": 5258, "platform": "youtube", "handle": "UCjYD2Rcj3T4DgDa98k9OU7A",
                  "followers": 7020, "avg_views": None, "avg_comments": None, "engagement_rate": None,
                  "low_reach_flagged": 0}]
    items = [
        _session_item("new_creator", handle="gcrustypork", views=0, likes=0, comments=0, avg_views=0,
                      channel_url="https://www.youtube.com/channel/UCjYD2Rcj3T4DgDa98k9OU7A"),
        _session_item("new_creator", handle="gcrustypork2", views=0, likes=0, comments=0, avg_views=0,
                      channel_id="UCjYD2Rcj3T4DgDa98k9OU7A"),
    ]
    visible, counts = _apply_reach_display_gate(_FakeGateConn(pool_rows), items)
    assert [it["payload"]["handle"] for it in visible] == ["gcrustypork", "gcrustypork2"]
    assert all(it["payload"]["followers"] == 7020 and it["payload"]["reach_status"] == "ok" for it in visible)
    assert counts["hidden_low_reach"] == 0


def test_session_reach_display_gate_payload_fast_path_exempts_zero_engagement() -> None:
    """池行缺 + 快照带 fast_path(attach 现透传)→ views/comments=0 是填充非实测,不判低触达;
    followers 未知 → 上墙标 analyzing(不再被误归低触达藏掉)。"""
    from app.domains.kol.search_sessions import _apply_reach_display_gate

    items = [_session_item("new_creator", handle="fastpath_only", views=0, comments=0, fast_path=True)]
    visible, counts = _apply_reach_display_gate(_FakeGateConn([]), items)
    assert len(visible) == 1 and visible[0]["payload"]["reach_status"] == "analyzing"
    assert counts["hidden_low_reach"] == 0 and counts["visible_analyzing"] == 1


def test_session_reach_display_gate_fail_open_on_db_error() -> None:
    from app.domains.kol.search_sessions import _apply_reach_display_gate

    items = [_session_item("new_creator", handle="whoever", views=1)]
    visible, counts = _apply_reach_display_gate(_FakeGateConn([], fail=True), items)
    assert visible == items  # 池查询炸 → fail-open 全放行(过滤器不当故障放大器)
    assert counts.get("error") == "pool_lookup_failed"


def test_session_reach_display_gate_switch_off_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.kol.search_sessions import _apply_reach_display_gate

    monkeypatch.setenv("VKPI_DISCOVERY_REACH_FLOOR_ENABLED", "0")
    items = [_session_item("new_creator", handle="tiny", followers=2)]
    visible, counts = _apply_reach_display_gate(_FakeGateConn([]), items)
    assert visible == items and counts["hidden_low_reach"] == 0


# ── 发现面「库内已有」分诊(12297 回流通道)+ 入库 followers 诚实口径 ────────────────


def test_triage_existing_matches_reach(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.kol import profile_discovery

    pool_rows = [
        {"id": 12297, "platform": "youtube", "handle": "UCqfGG_OEwUbeaYImjOGblFw",
         "followers": 2, "avg_views": None, "avg_comments": None, "engagement_rate": None,
         "low_reach_flagged": 0},
        {"id": 500, "platform": "youtube", "handle": "healthy",
         "followers": 88_000, "avg_views": None, "avg_comments": None, "engagement_rate": None,
         "low_reach_flagged": 0},
        {"id": 501, "platform": "youtube", "handle": "nullrow",
         "followers": None, "avg_views": None, "avg_comments": None, "engagement_rate": None,
         "low_reach_flagged": 0},
    ]
    monkeypatch.setattr(profile_discovery, "get_conn", lambda: _FakeGateConn(pool_rows))
    ignited: list[int] = []
    import app.domains.discovery.buildout as buildout

    monkeypatch.setattr(buildout, "ignite_profile_buildout",
                        lambda pid, **kw: ignited.append(int(pid)) or {"tier": "light"})

    matches = [
        {"handle": "UCqfGG_OEwUbeaYImjOGblFw", "history_kol_pool_id": 12297},
        {"handle": "healthy", "historical_match": {"kol_pool_id": 500}},
        {"handle": "nullrow", "history_kol_pool_id": 501},
        {"handle": "no_pool_row", "history_kol_pool_id": 999},  # 池行缺 → 放行不误杀
    ]
    kept, counts = profile_discovery._triage_existing_matches_reach(matches)

    assert {m["handle"] for m in kept} == {"healthy", "no_pool_row"}
    assert counts == {"low_reach": 1, "analyzing": 1}
    assert ignited == [501]  # followers 未知的库内行补点火 enrichment(分析后再 po)


def test_auto_enroll_writes_null_followers_when_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """入库诚实口径:发现项 followers 未知 → 落 NULL;绝不拿 avg_views 冒充、不编 0。"""
    from app.domains.kol import profile_discovery
    import app.domains.kol.profile_basics as profile_basics

    written: list[dict[str, Any]] = []

    def fake_writer(kol_pool_id: Any, profile_data: dict[str, Any], **_kw: Any) -> dict[str, Any]:
        written.append(dict(profile_data))
        return {"ok": True, "kol_pool_id": None}  # 不触发 buildout 点火分支

    monkeypatch.setattr(profile_basics, "write_kol_profile_basics", fake_writer)
    monkeypatch.setenv("KOL_AUTO_DEDUP_ENROLL", "0")

    creators = [
        _discovery_item("unknownfollowers", avg_views=7_777),   # 未知 → NULL(不再拿 avg_views 冒充)
        _discovery_item("knownfollowers", followers=42_000),    # 已知 → 原值
    ]
    enrolled = profile_discovery._auto_enroll_discoveries(creators)
    assert enrolled == 2
    by_handle = {w["handle"]: w for w in written}
    assert by_handle["unknownfollowers"]["followers"] is None
    assert by_handle["knownfollowers"]["followers"] == 42_000
