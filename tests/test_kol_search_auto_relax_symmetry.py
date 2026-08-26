"""加筛选与松筛选**同等可见**,还原**任何状态下都能用**(2026-08-26 复核纠偏)。

复核坐实的两条:

* **HIGH 5 加筛选完全静默。** 「plan 的 market / 粉丝下限提示 / 垂类推断 → 变成真硬筛」
  这条路只有松绑会进台账,加筛选一个字都不播报。系统能悄悄替操作员加上他从没说过的
  条件 —— 比不松绑更严重,因为他根本不知道自己被加了条件。
* **HIGH 4 「改回我的条件」回不去。** ``auto_relax=false`` 只关掉了放宽那一半,系统推断
  出来的国家 / 语言 / 垂类照样被硬加上去;而且早退路径把还原落点也一起带走了。

本文件只钉这两件事(放宽阶梯本身在 ``test_kol_search_auto_relax.py``)。
"""
from __future__ import annotations

from typing import Any

import pytest
from test_kol_search_auto_relax import RecordingEstimator, _make_pool, _route_harness

from app.domains.kol import search_auto_relax as relax


@pytest.fixture()
def estimator() -> RecordingEstimator:
    return RecordingEstimator(_make_pool())


def _plan(**overrides: Any) -> dict[str, Any]:
    """提议车道挂在计划上的那份提议:国家 + 语言两项都是**推断的**(操作员没说过)。"""
    proposal: dict[str, Any] = {
        "filters": {"countries": ["us"], "languages": ["en"]},
        "relaxable_fields": ["countries", "languages"],
        "facets": {
            "countries": {"source": "rule", "evidence": "你没点名国家，这是默认的主力市场"},
            "languages": {"source": "model", "evidence": "你没点名语言，这是按你的描述推断的"},
        },
        "source": "model",
        "degraded": False,
    }
    proposal.update(overrides)
    return {"filter_proposal": proposal}


# ── HIGH 5:系统加了什么,必须和松了什么一样播报 ────────────────────────────


def test_additions_are_reported_never_applied_silently(estimator: RecordingEstimator) -> None:
    _effective, payload = relax.run_auto_relax({"filters": {}}, _plan(), estimator=estimator, target=30)
    added = {item["key"]: item for item in payload["added"]}
    assert set(added) == {"countries", "languages"}, "系统加的每一条都必须进台账"
    assert added["countries"]["action"] == relax.ACTION_ADD
    assert added["countries"]["values"] == ["us"]
    assert added["countries"]["removable"] is True, "他没说过的条件,必须能一键去掉"
    # 「为什么加」是提议侧的原话透传,不是本模块编的。
    assert added["countries"]["reason"] == "你没点名国家，这是默认的主力市场"
    assert added["languages"]["reason"] == "你没点名语言，这是按你的描述推断的"


def test_addition_reason_is_the_proposal_lanes_own_words() -> None:
    """拿提议车道真的产出跑一遍:理由逐字对齐,不是我这边自说自话。"""
    from app.domains.kol import smart_query_facets

    proposal = smart_query_facets.propose_facets("找镜头评测博主", {"market": "US"})
    plan = {"filter_proposal": proposal}
    filters, origins = relax.merge_plan_filters({}, {}, plan)
    _kept, _marked, added, _dropped = relax.split_additions(filters, origins, plan)
    for record in added:
        facet = proposal["facets"][relax.FILTER_KEY_TO_FACET[record["key"]]]
        assert record["reason"] == facet["evidence"], record["key"]
        assert facet["origin"] == smart_query_facets.ORIGIN_INFERRED, "明确项不算「系统加的」"


def test_operator_own_filters_are_never_reported_as_additions(estimator: RecordingEstimator) -> None:
    """他自己勾的、和提议车道判成「他原话说过的」,都不是系统加的 —— 不许摆到加项里去吓他。"""
    body = {"filters": {"countries": ["jp"]}}
    _effective, payload = relax.run_auto_relax(body, _plan(relaxable_fields=["languages"]), estimator=estimator)
    assert [item["key"] for item in payload["added"]] == ["languages"]
    assert payload["operator_filters"]["countries"] == ["jp"]


def test_quality_gates_are_never_reported_as_additions(estimator: RecordingEstimator) -> None:
    """合格线不是谁「加」的,是库的标准:既不进加项,也不会被「改回我的条件」去掉。"""
    filters = {"gear_content": "yes", "languages": ["en"]}
    origins = {"gear_content": relax.ORIGIN_MODEL, "languages": relax.ORIGIN_MODEL}
    kept, _marked, added, _dropped = relax.split_additions(filters, origins, None, keep=False)
    assert [item["key"] for item in added] == []
    assert kept == {"gear_content": "yes"}, "合格线留下,系统加的语言去掉"
    assert relax.operator_filters(filters, origins) == {"gear_content": "yes"}


# ── HIGH 4:还原在任何状态下都回得去 ────────────────────────────────────────


@pytest.mark.parametrize("body", [{}, {"auto_relax": False}, {"auto_filters": False}])
def test_restore_target_survives_every_status(body: dict[str, Any], estimator: RecordingEstimator) -> None:
    payload = relax.run_auto_relax({"filters": {"countries": ["jp"]}, **body}, _plan(), estimator=estimator)[1]
    assert payload["operator_filters"] == {"countries": ["jp"]}, payload["status"]
    assert payload["restore_request"] == {"auto_relax": False, "auto_filters": False}


def test_estimate_failure_still_hands_back_the_restore_target() -> None:
    """估不出人数(早退)也必须带着还原落点与加项 —— 上一版正是这里把它们弄丢的。"""

    def _boom(_filters: Any) -> dict[str, Any]:
        raise RuntimeError("count failed")

    payload = relax.run_auto_relax({"filters": {"countries": ["jp"]}}, _plan(), estimator=_boom)[1]
    assert payload["status"] == relax.STATUS_UNAVAILABLE
    assert payload["unavailable_reason"] == "estimate_failed"
    assert payload["operator_filters"] == {"countries": ["jp"]}
    assert payload["restore_request"] == relax.RESTORE_REQUEST
    assert [item["key"] for item in payload["added"]] == ["languages"]


def test_missing_estimator_also_keeps_the_restore_target() -> None:
    filters, origins = relax.merge_plan_filters({}, {}, _plan())
    payload = relax.plan_auto_relax(filters, origins, estimator=None, target=30, plan=_plan())
    assert payload["status"] == relax.STATUS_UNAVAILABLE
    assert payload["unavailable_reason"] == "estimator_missing"
    assert payload["operator_filters"] == {}
    assert [item["key"] for item in payload["added"]] == ["countries", "languages"]


def test_restore_request_really_returns_the_operator_conditions(estimator: RecordingEstimator) -> None:
    """把 ``restore_request`` 原样送回来,就必须真的回到操作员自己的条件。"""
    body = {"filters": {"countries": ["jp"]}, **relax.RESTORE_REQUEST}
    effective, payload = relax.run_auto_relax(body, _plan(), estimator=estimator)
    assert effective == {"countries": ["jp"]}, "系统加的语言必须消失,他勾的国家必须留下"
    assert payload["status"] == relax.STATUS_DISABLED
    assert payload["added"] == []
    assert [item["key"] for item in payload["added_dropped"]] == ["languages"]
    assert all(item["dropped"] is True for item in payload["added_dropped"])
    assert estimator.calls == [], "还原这一次不该再跑任何估算"


def test_auto_relax_off_alone_does_not_pretend_to_be_a_restore(estimator: RecordingEstimator) -> None:
    """只关放宽 = 加项仍在生效。台账必须如实带着加项,界面才不会说「已按你原来的条件」。"""
    effective, payload = relax.run_auto_relax({"auto_relax": False}, _plan(), estimator=estimator)
    assert effective == {"countries": ["us"], "languages": ["en"]}
    assert payload["status"] == relax.STATUS_DISABLED
    assert [item["key"] for item in payload["added"]] == ["countries", "languages"]
    assert payload["operator_filters"] == {}


def test_additions_can_be_dropped_one_by_one(estimator: RecordingEstimator) -> None:
    body = {"dropped_auto_filters": ["languages"]}
    effective, payload = relax.run_auto_relax(body, _plan(), estimator=estimator, target=1)
    assert effective["countries"] == ["us"] and "languages" not in effective
    assert [item["key"] for item in payload["added"]] == ["countries"]
    assert [item["key"] for item in payload["added_dropped"]] == ["languages"]


def test_unreadable_drop_list_changes_nothing(estimator: RecordingEstimator) -> None:
    """认不出的形状 = 没点掉(失败方向:保持现状,绝不擅自去掉他没点的条件)。"""
    assert relax.dropped_auto_filter_keys({"dropped_auto_filters": "countries"}) == frozenset()
    payload = relax.run_auto_relax({"dropped_auto_filters": None}, _plan(), estimator=estimator)[1]
    assert [item["key"] for item in payload["added"]] == ["countries", "languages"]


def test_plan_auto_relax_alone_still_reports_additions(estimator: RecordingEstimator) -> None:
    """直接调本函数(不走一步到位入口)也拿得到加项 —— 加筛选没有静默的代码路径。"""
    payload = relax.plan_auto_relax(
        {"languages": ["en"]},
        {"languages": relax.ORIGIN_MODEL},
        estimator=estimator,
        target=30,
        plan=_plan(),
    )
    assert [item["key"] for item in payload["added"]] == ["languages"]
    assert payload["operator_filters"] == {}


# ── 路由接线:整条链上加项与还原都到得了操作员眼前 ──────────────────────────


def test_route_restore_drops_the_system_added_filter_end_to_end(
    monkeypatch: pytest.MonkeyPatch, estimator: RecordingEstimator
) -> None:
    import asyncio

    from app.api.routers import vkpi_kol_pool_search as route

    recall_calls = _route_harness(monkeypatch, estimator)
    response = asyncio.run(
        route.smart_kol_search(
            {"input": "美国的镜头评测博主", "create_session": False, **relax.RESTORE_REQUEST},
            staff={"id": 42},
        )
    )

    ledger = response["result"]["auto_relax"]
    assert ledger["status"] == relax.STATUS_DISABLED
    # 路由那份提议里语言是推断的、国家是操作员原话里的:还原只去掉前者。
    assert [item["key"] for item in ledger["added_dropped"]] == ["languages"]
    assert "languages" not in recall_calls[0]["filters"]
    assert recall_calls[0]["filters"]["countries"] == ["us"]
    assert ledger["restore_request"] == relax.RESTORE_REQUEST


def test_route_reports_the_addition_when_nothing_was_relaxed(
    monkeypatch: pytest.MonkeyPatch, estimator: RecordingEstimator
) -> None:
    import asyncio

    from app.api.routers import vkpi_kol_pool_search as route

    _recall_calls = _route_harness(monkeypatch, estimator)
    response = asyncio.run(
        route.smart_kol_search(
            {"auto_relax": True, "auto_filters": True, "input": "美国的镜头评测博主", "create_session": False, "result_limit": 1},
            staff={"id": 42},
        )
    )

    ledger = response["result"]["auto_relax"]
    assert ledger["status"] == relax.STATUS_NOT_NEEDED, "人够用,一格都没松"
    # 一格都没松,但系统确实替他加了语言这一条 —— 台账照样得说。
    assert [item["key"] for item in ledger["added"]] == ["languages"]
    # 国家是提议车道按他原话判成明确项的 —— 那是他的条件,不是系统加的。
    assert ledger["operator_filters"] == {"countries": ["us"]}


def test_route_defaults_the_whole_feature_off_until_the_two_highs_are_fixed(
    monkeypatch: pytest.MonkeyPatch, estimator: RecordingEstimator
) -> None:
    """路由层默认关(2026-08-26)。

    对抗复核坐实两条未修完的缺陷:①预估数与召回腿未逐字对齐,用不准的数驱动自动放宽
    比不放宽更糟;②「自动加筛选」这半边完全静默,能悄悄替操作员加上他没说过的条件。
    在两条修完之前,操作员不显式开启就一格都不许动 —— 这条测试钉住这个默认,
    修完后连同 vkpi_kol_pool_search.py 里那两行 setdefault 一起删。
    """
    import asyncio

    from app.api.routers import vkpi_kol_pool_search as route

    _recall_calls = _route_harness(monkeypatch, estimator)
    response = asyncio.run(
        route.smart_kol_search(
            {"input": "美国的镜头评测博主", "create_session": False, "result_limit": 30},
            staff={"id": 42},
        )
    )
    ledger = response["result"]["auto_relax"]
    assert ledger["status"] == relax.STATUS_DISABLED, "路由默认必须是关的"
    assert not ledger.get("relaxed"), "默认关时不许放宽任何一格"
    assert not ledger.get("added"), "默认关时不许自动加任何筛选"
