"""自动升级入队的 payload 必须与前端「全网查找」那条腿逐字等价(车道目标 2 / 9.3)。

为什么这件事值一整个文件:``_smart_profile_payload`` 对每个缺席的键都有自己的兜底,
而兜底值和前端真正送的值**不一样**。少送一个键,跑出来的东西看起来像同一件事,实际是
另一份合同:

    online_qualification_spec 缺席 → 发现上限 50 → 15
    local_qualification_spec 缺席 → advance_limit 封顶 30 → 15
    new_discovery_per_platform_limit 缺席 → 20 → 45(跟着 new_discovery_limit 走)
    representative_video_limit 缺席 → 1 → None
    candidate_limit / creator_quota / reviewer_quota / bucket_policy 缺席 → 各自另一套默认

所以本文件不比对「我写的常量等于我写的常量」,而是:
  1. 拿两份 **body**(一份逐字转写自前端 ``smartKolSearchProfileAdvanceJob``,一份由
     ``escalation_advance_body`` 从最贫瘠的 /kol-smart-search body 补出来),
  2. 桩掉落库那一步,各跑一次真实的 ``enqueue_smart_search_profile_advance``,
  3. 断言两份**落库 payload 全等**。
另加一道防漂移:直接读 TS 源文件,断言 Python 侧的策略镜像与前端数值一致。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.domains.kol import profile_discovery_queue as queue
from app.domains.kol import search_escalation as escalation


ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ROOT / "frontend/src/components/vkpi/cockpit/components"
QUERY = "portrait lens creators"
SESSION_ID = 1234
STAFF = {"id": 41, "staff_id": 41, "user_id": 41}
PLATFORMS = ["youtube", "instagram", "tiktok"]
FILTERS = {
    "platforms": PLATFORMS,
    "countries": ["US"],
    "languages": ["en"],
    "followers_min": 5000,
    "gear_content": "yes",
}
# 本刀点名要求逐字等价的键(其余键一并断言全等,这几个单独再点一次名)。
# ``online_qualification_spec`` 本身不进 payload —— _smart_profile_payload 只拿它判一个
# 布尔,所以在 payload 这一层它的等价体现为 ``_smart_online_30_contract``;两份 **body**
# 上的 spec 另有一条断言直接比对。
NAMED_KEYS = (
    "_smart_online_30_contract",
    "new_discovery_limit",
    "new_discovery_per_platform_limit",
    "new_discovery_platforms",
    "advance_limit",
    "max_posts",
    "representative_video_limit",
)


@pytest.fixture
def captured_payloads(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """桩掉落库/会话写入,只留下 payload 的构造这一段真实代码路径。"""
    payloads: list[dict[str, Any]] = []

    def fake_enqueue(payload: dict[str, Any], session_id: int):
        payloads.append(json.loads(json.dumps(payload, default=str)))
        return {"id": 9001, "status": "queued"}, True, {}

    monkeypatch.setattr(queue, "_enqueue_smart_profile_payload", fake_enqueue)
    monkeypatch.setattr(
        queue.search_sessions,
        "ensure_session_for_result",
        lambda **_kw: {"id": SESSION_ID, "query_text": QUERY, "query_type": "text_recall"},
    )
    monkeypatch.setattr(
        queue.search_sessions, "update_session_result_summary", lambda *_a, **_kw: {}
    )
    monkeypatch.setattr(queue.search_sessions, "get_session", lambda _sid: {"id": SESSION_ID})
    # 围栏凭证按会话/员工签发,与本测试的口径无关;钉成常量以免它盖过真正的差异。
    monkeypatch.setattr(
        queue, "build_search_session_provider_fence", lambda **_kw: {"fence": "stubbed"}
    )
    return payloads


def _frontend_advance_body() -> dict[str, Any]:
    """逐字转写 frontend/src/services/vkpi/kolPool-api.search.ts 的
    ``smartKolSearchProfileAdvanceJob``,参数取 SmartKolInputPanel.controller.ts
    的 ``queueTextAdvance``(策略 balanced)。"""
    return {
        "input": QUERY,
        "objective": "prospective_growth",
        "queue_pipeline": True,
        "include_new_discovery": True,
        "new_discovery_platforms": PLATFORMS,
        "market": "US",
        "languages": ["en"],
        "profile_types": ["creator"],
        "local_qualification_spec": dict(escalation.LOCAL_QUALIFICATION_SPEC),
        "online_qualification_spec": dict(escalation.ONLINE_QUALIFICATION_SPEC),
        "session_id": SESSION_ID,
        "exclude_chinese": True,
        "candidate_limit": 500,
        "limit": 30,
        "creator_quota": 18,
        "reviewer_quota": 12,
        "result_limit": 30,
        "search_strategy": "balanced",
        "filters": FILTERS,
        "platforms": PLATFORMS,
        "bucket_policy": {"core_vertical": 18, "expansion": 9, "exploration": 3},
        "advance_limit": 30,
        "max_posts": 12,
        "representative_video_limit": 1,
        "new_discovery_limit": 45,
        "new_discovery_per_platform_limit": 20,
        "new_discovery_per_platform_limits": {"youtube": 50, "instagram": 20, "tiktok": 20},
    }


def _recall_body() -> dict[str, Any]:
    """**真实生产 body**:逐字转写 ``kolPool-api.search.ts`` 的 ``smartKolSearch``,
    参数取 ``SmartKolInputPanel.controller.ts`` 的 ``run()``(策略 balanced)。

    这里以前放的是一份「刻意最贫瘠」的 body —— 发现相关的键一个都不带。那份 body 在生产里
    不存在,而且它恰好绕开了本文件唯一要防的那类病:**键在、值也非假、但轴不对**。
    真实 body **一定**带 ``max_posts: 3``(controller.ts:436 写死 → kolPool-api.search.ts:98
    无条件透传),那是 URL 预览深度(url_deep_crawl_helpers._max_posts 是唯一读它的地方);
    抓取轴上同名键要的是 12。等价断言必须踩在这份 body 上,否则就是假绿。

    ``auto_relax`` / ``auto_filters`` / ``dropped_auto_filters`` 只在操作员按了
    「改回我的条件」时才出现,且 ``_smart_profile_payload`` 一个都不读,对等价性无影响。
    """
    return {
        "input": QUERY,
        "mode": "auto",
        "objective": "prospective_growth",
        "create_session": True,
        "response_projection": "smart_local_compact_v1",
        "max_posts": 3,
        "candidate_limit": 500,
        "limit": 30,
        "result_limit": 30,
        "creator_quota": 18,
        "reviewer_quota": 12,
        "search_strategy": "balanced",
        "filters": FILTERS,
        "platforms": PLATFORMS,
        "bucket_policy": {"core_vertical": 18, "expansion": 9, "exploration": 3},
        "market": "US",
        "languages": ["en"],
        "profile_types": ["creator"],
        "local_qualification_spec": dict(escalation.LOCAL_QUALIFICATION_SPEC),
        "exclude_chinese": True,
    }


def _naive_body() -> dict[str, Any]:
    """真实 body 去掉本地筛选口径 —— 用来证明「补齐」这件事真的有作用(反证组)。"""
    return {k: v for k, v in _recall_body().items() if k != "local_qualification_spec"}


def _enqueue(body: dict[str, Any]) -> dict[str, Any]:
    return queue.enqueue_smart_search_profile_advance(query_text=QUERY, body=body, staff=STAFF)


# ── 1. 两份 body 落出来的 payload 必须全等 ──


def test_escalation_payload_is_word_for_word_equal_to_the_frontend_advance_leg(
    captured_payloads: list[dict[str, Any]]
) -> None:
    _enqueue(_frontend_advance_body())

    decision = escalation.decide_escalation(
        body=_recall_body(),
        recall_result={
            "local_qualification": {
                "schema": escalation.LOCAL_QUALIFICATION_SCHEMA,
                "policy": {"target_count": 30},
                "qualified_returned_count": 11,
                "shortfall": 19,
            }
        },
        visible_session_id=SESSION_ID,
    )
    assert decision.escalate is True
    _enqueue(
        escalation.escalation_advance_body(
            _recall_body(),
            platforms=decision.platforms,
            query_text=QUERY,
            session_id=SESSION_ID,
        )
    )

    # body 这一层:两条腿带的在线合同必须是同一份。
    assert escalation.escalation_advance_body(
        _recall_body(), platforms=decision.platforms, query_text=QUERY, session_id=SESSION_ID
    )["online_qualification_spec"] == _frontend_advance_body()["online_qualification_spec"]

    assert len(captured_payloads) == 2
    frontend, escalated = captured_payloads
    for key in NAMED_KEYS:
        assert escalated[key] == frontend[key], f"{key} 不等价:{escalated[key]!r} != {frontend[key]!r}"
    differing = {
        key: (frontend.get(key), escalated.get(key))
        for key in set(frontend) | set(escalated)
        if frontend.get(key) != escalated.get(key)
    }
    assert not differing, f"两条腿的 payload 有差异:{differing}"


def test_the_named_keys_really_differ_without_the_equivalence_helper(
    captured_payloads: list[dict[str, Any]]
) -> None:
    """反证:不补齐就直接入队,这些键确实是另一套值 —— 证明上面那条断言不是空转。"""
    _enqueue(_frontend_advance_body())
    _enqueue({**_naive_body(), "include_new_discovery": True, "session_id": SESSION_ID})
    frontend, naive = captured_payloads
    assert naive["advance_limit"] == 15 and frontend["advance_limit"] == 30
    assert naive["new_discovery_limit"] == 15 and frontend["new_discovery_limit"] == 45
    assert naive["new_discovery_per_platform_limit"] == 15
    assert naive["representative_video_limit"] is None
    assert naive["_smart_online_30_contract"] is False
    assert naive["_smart_local_30_contract"] is False
    # 这一行以前写的是「这一个的默认值恰好相同」—— 那是因为当时的 _recall_body 不带
    # max_posts。真实生产 body 带着 URL 预览深度 3:不摘掉再按抓取轴注入,全网抓取
    # 每人的取样帖数就从 12 掉到 3(1/4)。
    assert naive["max_posts"] == 3 and frontend["max_posts"] == 12
    # /kol-smart-search 的 mode 是「URL 还是文本」的路由开关,不是抓取深度;
    # 不摘掉它就会被 advance_mode 读走,把 account_deep 换成 auto。
    assert naive["advance_mode"] == "auto" and frontend["advance_mode"] == "account_deep"


def test_local_qualification_spec_is_injected_when_the_client_omits_it() -> None:
    body = escalation.escalation_advance_body(
        _naive_body(), platforms=("youtube",), query_text=QUERY
    )
    assert queue._requests_smart_local_30(body) is True
    assert queue._requests_smart_online_30(body) is True


def test_recall_axis_keys_never_ride_along_into_the_crawl_axis() -> None:
    """本文件的主钉子:同名不同轴的键必须先摘掉,再按抓取轴的真值注入。

    ``max_posts`` 是全场唯一一个**值非假**的漏点 —— 「假值才覆盖」的补齐方式对它无效,
    3 不是假值。漏了它,每次界面文字搜索触发的全网抓取,每人取样帖数都掉到 1/4。
    """
    assert _recall_body()["max_posts"] == 3, "真实 /kol-smart-search body 必须带 max_posts: 3"
    body = escalation.escalation_advance_body(
        _recall_body(), platforms=("tiktok",), query_text=QUERY, session_id=SESSION_ID
    )
    assert body["max_posts"] == escalation.MAX_POSTS == 12
    # 这三个是召回那一次请求自己的键,抓取轴上没有它们的位置。
    for leaked in ("mode", "create_session", "response_projection"):
        assert leaked not in body, f"{leaked} 不该跟着进抓取轴"


def test_operator_supplied_crawl_values_are_never_overwritten() -> None:
    """抓取轴上操作员显式送来的值不动;缺席的按策略档补。"""
    lean = {
        key: value for key, value in _recall_body().items()
        if key not in ("creator_quota", "reviewer_quota", "bucket_policy")
    }
    body = escalation.escalation_advance_body(
        {**lean, "advance_limit": 7, "search_strategy": "expansion"},
        platforms=("tiktok",),
        query_text=QUERY,
    )
    assert body["advance_limit"] == 7
    # 策略换成 expansion 时,补出来的数字也跟着换成 expansion 那一档。
    assert body["new_discovery_limit"] == 50
    assert body["creator_quota"] == 21 and body["reviewer_quota"] == 9
    # 平台永远是算好的那一份,不许被 body 里的旧值顶掉。
    assert body["new_discovery_platforms"] == ["tiktok"]


# ── 2. 防漂移:Python 侧的策略镜像必须等于前端 TS 里的数值 ──


def _ts(name: str) -> str:
    return (COMPONENTS / name).read_text(encoding="utf-8")


def _number(source: str, field: str, *, within: str | None = None) -> int:
    """读 ``field: 123`` 形式的对象字段。"""
    text = within if within is not None else source
    match = re.search(rf"\b{field}\s*:\s*([0-9_]+)", text)
    assert match, f"TS 源里找不到字段 {field}"
    return int(match.group(1).replace("_", ""))


def _const(source: str, name: str) -> int:
    """读 ``export const NAME = 123;`` 形式的常量。"""
    match = re.search(rf"export const {name}\s*=\s*([0-9_]+)", source)
    assert match, f"TS 源里找不到常量 {name}"
    return int(match.group(1).replace("_", ""))


def _strategy_block(source: str, key: str) -> str:
    match = re.search(rf"\n  {key}: \{{(.*?)\n  \}},", source, re.S)
    assert match, f"TS 源里找不到策略 {key}"
    return match.group(1)


def test_python_strategy_mirror_matches_the_frontend_policy_table() -> None:
    policy_ts = _ts("SmartKolInputPanel.SearchPolicy.tsx")
    assert escalation.RESULT_LIMIT == _const(policy_ts, "KOL_SEARCH_RESULT_LIMIT")
    limits_block = re.search(r"KOL_SEARCH_PER_PLATFORM_LIMITS.*?\{(.*?)\}", policy_ts, re.S)
    assert limits_block
    assert escalation.PER_PLATFORM_LIMITS == {
        name: int(value) for name, value in re.findall(r"(\w+):\s*(\d+)", limits_block.group(1))
    }
    for key, (creator, reviewer, discovery, per_platform, core, expansion, exploration) in (
        escalation.STRATEGY_POLICY.items()
    ):
        block = _strategy_block(policy_ts, key)
        assert creator == _number(policy_ts, "creatorQuota", within=block)
        assert reviewer == _number(policy_ts, "reviewerQuota", within=block)
        assert discovery == _number(policy_ts, "newDiscoveryLimit", within=block)
        assert per_platform == _number(policy_ts, "perPlatformLimit", within=block)
        bucket = re.search(r"bucketPolicy:\s*\{(.*?)\}", block, re.S)
        assert bucket
        assert (core, expansion, exploration) == tuple(
            int(value) for _name, value in re.findall(r"(\w+):\s*(\d+)", bucket.group(1))
        )


def test_python_spec_mirrors_match_the_frontend_contracts() -> None:
    online_ts = _ts("SmartKolInputPanel.OnlineQualified.ts")
    assert escalation.ONLINE_QUALIFICATION_SPEC["version"] == re.search(
        r'ONLINE_QUALIFICATION_SPEC = Object\.freeze\(\{\s*version:\s*"([^"]+)"', online_ts
    ).group(1)
    assert escalation.ONLINE_QUALIFICATION_SPEC["target_count"] == _number(
        online_ts, "target_count"
    )
    strict = re.search(r"STRICT_ONLINE_PLATFORMS = Object\.freeze\(\[(.*?)\]", online_ts, re.S)
    assert list(escalation.ONLINE_DISCOVERY_PLATFORMS) == re.findall(r'"([^"]+)"', strict.group(1))

    local_ts = _ts("SmartKolInputPanel.LocalQualified.ts")
    assert escalation.LOCAL_QUALIFICATION_SPEC["version"] == re.search(
        r'LOCAL_QUALIFICATION_SPEC = Object\.freeze\(\{\s*version:\s*"([^"]+)"', local_ts
    ).group(1)
    assert escalation.LOCAL_QUALIFICATION_SPEC["target_count"] == _const(
        local_ts, "LOCAL_QUALIFIED_TARGET"
    )

    controller_ts = _ts("SmartKolInputPanel.controller.ts")
    advance_call = re.search(
        r"smartKolSearchProfileAdvanceJob\(apiToken, query, \{(.*?)\n      \}\);", controller_ts, re.S
    )
    assert advance_call, "controller 里找不到全网查找那条腿的调用"
    assert escalation.MAX_POSTS == _number(controller_ts, "maxPosts", within=advance_call.group(1))
    assert escalation.REPRESENTATIVE_VIDEO_LIMIT == _number(
        controller_ts, "representativeVideoLimit", within=advance_call.group(1)
    )
    assert escalation.CANDIDATE_LIMIT == _number(
        controller_ts, "candidateLimit", within=advance_call.group(1)
    )
