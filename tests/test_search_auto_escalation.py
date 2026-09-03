"""搜索自动升级:两段求值、平台保真、闸与空态。

对应车道目标 1 / 3 / 5 / 6。三件事必须被钉死,否则这刀就是在制造新的谎:

1. **不升级的纯库内搜索零新增 IO**。第一段是纯函数,所以本文件把 get_conn /
   redis / 日配额 / 预算 / 入队全部换成「一碰就炸」的桩:任何一条不升级的路径只要
   多摸一次库,测试立刻红。
2. **操作员的平台选择不许被系统改写**(9.4)。只勾了没有联网发现腿的平台时,升级
   必须停下并说人话,绝不能悄悄改去搜 YouTube/Instagram/TikTok。
3. **诚实空态**。判不出来(没有命名筛选口径 / 没明说要找新人)时返回 None,面板保持
   「未请求」;判得出来但决定不升级时,给的是人话原因,不是内部术语。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.domains.kol import search_escalation as escalation
from app.domains.kol import search_escalation_contract as escalation_contract
from app.domains.kol import profile_discovery, profile_online_qualification


LOCAL_SCHEMA = escalation.LOCAL_QUALIFICATION_SCHEMA
STAFF = {"id": 41, "staff_id": 41}
# 操作员这一刻正在看的那条会话。/kol-smart-search 默认会先建一条并把 id 放进 session_body;
# 没有它就没有面板,升级一律不许发生(见 test_escalation_needs_a_visible_session)。
SESSION_ID = 1234


def _session_body(body: dict[str, Any]) -> dict[str, Any]:
    return {**body, "session_id": SESSION_ID, "create_session": False}


def _recall(qualified: int, *, target: int = 30, schema: str = LOCAL_SCHEMA) -> dict[str, Any]:
    """一份带命名筛选口径的召回结果(只保留升级判定真正读的字段)。"""
    return {
        "items": [{"id": index} for index in range(qualified)],
        "local_qualification": {
            "schema": schema,
            "status": "ready" if qualified >= target else "shortfall",
            "policy": {"target_count": target},
            "qualified_count": qualified,
            "qualified_returned_count": qualified,
            "shortfall": max(0, target - qualified),
        },
    }


def _body(**overrides: Any) -> dict[str, Any]:
    """一份 /kol-smart-search 会收到的 body(前端总是显式带 objective)。"""
    base: dict[str, Any] = {
        "input": "portrait lens creators",
        "mode": "auto",
        "objective": "prospective_growth",
        "create_session": True,
        "response_projection": "smart_local_compact_v1",
        "search_strategy": "balanced",
    }
    base.update(overrides)
    return base


@pytest.fixture
def no_io(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """把所有会产生 DB / Redis / 队列往返的入口换成会爆炸的桩。

    返回的列表始终为空 —— 它只是让失败信息说清楚是哪一条被摸了。
    """
    touched: list[str] = []

    def boom(name: str):
        def _explode(*_args: Any, **_kwargs: Any):
            touched.append(name)
            raise AssertionError(f"不升级的路径不该碰 {name}")

        return _explode

    from app.db import connection as db_connection
    from app.domains.costs import budget_guard
    from app.platform import rate_limit_store, user_quota

    monkeypatch.setattr(db_connection, "get_conn", boom("get_conn"))
    monkeypatch.setattr(rate_limit_store, "_get_redis", boom("redis"))
    monkeypatch.setattr(user_quota, "used_today", boom("user_quota.used_today"))
    monkeypatch.setattr(user_quota, "daily_limit", boom("user_quota.daily_limit"))
    monkeypatch.setattr(user_quota, "consume", boom("user_quota.consume"))
    monkeypatch.setattr(budget_guard, "check_budget_decision", boom("budget_guard"))
    monkeypatch.setattr(
        profile_discovery, "enqueue_smart_search_profile_advance", boom("enqueue")
    )
    return touched


# ── 1. 第一段是纯函数:不升级 = 零新增 DB / Redis 往返 ──


@pytest.mark.parametrize(
    "body, recall, expected_reason, expect_payload",
    [
        # 操作员自己已经要了全网 —— 既有分支处理,这里什么都不做。
        (_body(include_new_discovery=True), _recall(3), "already_requested", False),
        # 目标是找已有合作证据的人,不该去补新人。
        (_body(objective="existing_evidence"), _recall(3), "objective_not_new_people", False),
        # 没跑命名筛选口径 → 没有「精准命中数」这个量 → 不许替人做主(诚实空态)。
        (_body(), {"items": []}, "no_local_contract", False),
        (_body(), _recall(3, schema="something_else"), "no_local_contract", False),
        # 库里已经凑够 30 个 → 不需要补。
        (_body(), _recall(30), "local_target_met", True),
        (_body(), _recall(31), "local_target_met", True),
    ],
)
def test_not_escalating_paths_touch_no_database_or_redis(
    no_io: list[str], body: dict, recall: dict, expected_reason: str, expect_payload: bool
) -> None:
    decision = escalation.decide_escalation(body=body, recall_result=recall, visible_session_id=SESSION_ID)
    assert decision.escalate is False
    assert decision.reason_code == expected_reason

    payload = escalation.auto_escalated_discovery_payload(
        body=body, session_body=_session_body(body), recall_result=recall, recall_query="portrait lens creators", staff=STAFF
    )
    if expect_payload:
        # 判得出来但决定不补:给人话,不给内部术语。
        assert payload is not None
        assert payload["status"] == "not_escalated"
        assert payload["escalation"]["reason"] == expected_reason
        assert payload["escalation"]["reason_human"]
    else:
        # 判不出来:面板保持「未请求」的诚实空态,不要拿一句话去填一个空位。
        assert payload is None
    assert touched_nothing(no_io)


def touched_nothing(touched: list[str]) -> bool:
    return not touched


def test_shortfall_decides_to_escalate_without_touching_io(no_io: list[str]) -> None:
    decision = escalation.decide_escalation(body=_body(), recall_result=_recall(12), visible_session_id=SESSION_ID)
    assert decision.escalate is True
    assert decision.reason_code == "local_shortfall"
    assert (decision.target_count, decision.qualified_count, decision.shortfall) == (30, 12, 18)
    # 第一段自己不查配额也不查预算 —— 那是第二段的事。
    assert touched_nothing(no_io)


def test_escalation_needs_a_visible_session(no_io: list[str]) -> None:
    """create_session=false(只读搜索)时不许升级,也不许自己新建一条会话。

    升级的意义是「人能在面板上看着它跑完」。没有会话就没有面板,后台却真的在花钱抓 ——
    那是替人做主又不给他看。顺带杜绝自动路径偷偷 mint 一条操作员从未见过的会话。
    """
    body = _body(create_session=False)
    decision = escalation.decide_escalation(
        body=body, recall_result=_recall(3), visible_session_id=None
    )
    assert decision.escalate is False
    assert decision.reason_code == "no_visible_session"
    payload = escalation.auto_escalated_discovery_payload(
        body=body, session_body=body, recall_result=_recall(3), recall_query="q", staff=STAFF
    )
    assert payload is None
    assert touched_nothing(no_io)


# ── 2. 平台是操作员的选择(9.4) ──


def test_online_platform_set_matches_the_online_qualification_module() -> None:
    assert set(escalation.ONLINE_DISCOVERY_PLATFORMS) == set(
        profile_online_qualification.ONLINE_SUPPORTED_PLATFORMS
    )


@pytest.mark.parametrize("key", ["platforms", "new_discovery_platforms", "discovery_platforms"])
def test_operator_platform_choice_is_never_widened(no_io: list[str], key: str) -> None:
    """只勾了没有联网发现腿的平台 → 不升级,且绝不改去搜别的平台。"""
    body = _body(**{key: ["facebook"]})
    decision = escalation.decide_escalation(body=body, recall_result=_recall(4), visible_session_id=SESSION_ID)
    assert decision.escalate is False
    assert decision.reason_code == "no_online_leg_for_selected_platforms"
    assert decision.operator_selected_platforms is True
    assert decision.platforms == ()
    # 这是本条最要紧的一句:三大平台一个都不许出现在结果里。
    assert not set(decision.platforms) & set(escalation.ONLINE_DISCOVERY_PLATFORMS)

    payload = escalation.auto_escalated_discovery_payload(
        body=body, session_body=_session_body(body), recall_result=_recall(4), recall_query="q", staff=STAFF
    )
    assert payload["status"] == "not_escalated"
    assert payload["escalation"]["platforms"] == []
    assert touched_nothing(no_io)


def test_operator_platform_subset_is_kept_not_extended(no_io: list[str]) -> None:
    body = _body(platforms=["youtube", "facebook"])
    decision = escalation.decide_escalation(body=body, recall_result=_recall(4), visible_session_id=SESSION_ID)
    assert decision.escalate is True
    # 保留他选的 youtube;不因为「另外两个也支持」就替他加上。
    assert decision.platforms == ("youtube",)
    assert touched_nothing(no_io)


def test_filters_platforms_are_honoured_when_top_level_is_absent(no_io: list[str]) -> None:
    body = _body(filters={"platforms": ["tiktok"]})
    decision = escalation.decide_escalation(body=body, recall_result=_recall(4), visible_session_id=SESSION_ID)
    assert decision.platforms == ("tiktok",)
    assert touched_nothing(no_io)


def test_no_platform_choice_falls_back_to_the_three_online_platforms(no_io: list[str]) -> None:
    decision = escalation.decide_escalation(body=_body(), recall_result=_recall(4), visible_session_id=SESSION_ID)
    assert decision.operator_selected_platforms is False
    assert decision.platforms == escalation.ONLINE_DISCOVERY_PLATFORMS
    assert touched_nothing(no_io)


# ── 3. env 整体开关 ──


def test_env_switch_turns_the_whole_escalation_off(
    no_io: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(escalation.ENV_ENABLED, "0")
    decision = escalation.decide_escalation(body=_body(), recall_result=_recall(2), visible_session_id=SESSION_ID)
    assert decision.escalate is False and decision.reason_code == "disabled_by_env"
    payload = escalation.auto_escalated_discovery_payload(
        body=_body(), session_body=_session_body(_body()), recall_result=_recall(2), recall_query="q", staff=STAFF
    )
    assert payload["status"] == "not_escalated"
    assert payload["escalation"]["reason"] == "disabled_by_env"
    assert touched_nothing(no_io)


# ── 4. 第二段:配额与预算(只有判定要升级才跑) ──


@pytest.fixture
def enqueue_spy(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_enqueue(*, query_text: str, body: dict, staff: dict | None = None, **_kw: Any) -> dict:
        calls.append({"query_text": query_text, "body": body, "staff": staff})
        return {"status": "queued", "job": {"id": 9001}, "session_id": 777}

    monkeypatch.setattr(profile_discovery, "enqueue_smart_search_profile_advance", fake_enqueue)
    return calls


def _budget(monkeypatch: pytest.MonkeyPatch, allowed: bool, outcome: str = "") -> None:
    from app.domains.costs import budget_guard

    plan = {
        "contract": "budget_decision_v1",
        "allowed": allowed,
        "outcome": outcome or ("allowed" if allowed else "exhausted"),
    }
    monkeypatch.setattr(budget_guard, "check_budget_decision", lambda *_a, **_k: plan)


def _quota(monkeypatch: pytest.MonkeyPatch, *, limit: int, used: int) -> list[int]:
    from app.platform import user_quota

    consumed: list[int] = []
    monkeypatch.setattr(user_quota, "daily_limit", lambda _action: limit)
    monkeypatch.setattr(user_quota, "used_today", lambda _action, _staff, **_k: used)
    monkeypatch.setattr(user_quota, "consume", lambda _action, staff, **_k: consumed.append(staff))
    return consumed


def test_escalation_queues_the_job_on_the_visible_session(
    monkeypatch: pytest.MonkeyPatch, enqueue_spy: list[dict[str, Any]]
) -> None:
    _budget(monkeypatch, True)
    consumed = _quota(monkeypatch, limit=30, used=0)
    body = _body()
    # /kol-smart-search 先建了会话,后续这批任务必须挂在同一条上,否则面板等不到。
    session_body = {**body, "session_id": 1234, "create_session": False}

    payload = escalation.auto_escalated_discovery_payload(
        body=body, session_body=session_body, recall_result=_recall(11),
        recall_query="portrait lens creators", staff=STAFF,
    )

    assert payload["status"] == "queued"
    assert payload["job_id"] == 9001
    assert payload["provider_calls_performed"] is False
    assert payload["escalation"]["escalated"] is True
    assert payload["escalation"]["qualified_count"] == 11
    assert payload["escalation"]["shortfall"] == 19
    assert len(enqueue_spy) == 1
    assert enqueue_spy[0]["body"]["session_id"] == 1234
    # 复用可见会话,绝不带着 create_session 再开一条新的(前端那条腿也不带这个键)。
    assert "create_session" not in enqueue_spy[0]["body"]
    # 记账**不在这里**。理由见下一条测试。
    assert consumed == []


def test_the_escalated_work_is_billed_once_by_the_advance_endpoint_not_twice(
    monkeypatch: pytest.MonkeyPatch, enqueue_spy: list[dict[str, Any]]
) -> None:
    """同一份工作只能记一笔次数。

    前端每做一次文字搜索都必发 ``/kol-smart-search/profile-advance-job``
    (SmartKolInputPanel.controller.ts:540 恒开),那个端点在 ``user_quota._ROUTE_RULES``
    里是**空旗标 = 每个 POST 都记一笔**;而自动升级这条腿在同一个可见会话上撞的是同一个
    幂等键、同一份工作。自动升级若再自扣一笔,操作员的 30 次/天当场腰斩成 15 次
    (第 16 次起 429),连什么新工作都没产生的 already_queued 也照扣。

    下面两条路由规则的断言就是「不会双记」的实测依据:计费点在 advance 端点,不在这里。
    """
    from app.platform import user_quota

    prefix = "/api/admin/vkpi"
    assert user_quota.match_route("POST", f"{prefix}/kol-smart-search/profile-advance-job") == (
        "smart_search_online", ()
    ), "advance 端点必须仍是空旗标 = 每个 POST 都记一笔"
    # /kol-smart-search 只在 body 带发现旗标时才记账;自动升级这条路径一个旗标都没有,
    # 所以本次请求本身不记 —— 正因如此,当年才有人在这里补了一次自扣。
    action, flags = user_quota.match_route("POST", f"{prefix}/kol-smart-search")
    assert action == "smart_search_online" and flags == user_quota._DISCOVERY_BODY_FLAGS
    assert not any(_body().get(flag) for flag in flags)

    _budget(monkeypatch, True)
    consumed = _quota(monkeypatch, limit=30, used=0)
    for _ in range(3):
        payload = escalation.auto_escalated_discovery_payload(
            body=_body(), session_body=_session_body(_body()), recall_result=_recall(11),
            recall_query="portrait lens creators", staff=STAFF,
        )
        assert payload["status"] == "queued"
    assert consumed == [], "自动升级不许自扣 —— 记账在 advance 端点的中间件那一侧"


def test_the_escalation_gate_stands_down_when_the_global_quota_gate_is_off(
    monkeypatch: pytest.MonkeyPatch, enqueue_spy: list[dict[str, Any]]
) -> None:
    """⑦-1:全局闸关掉 = 中间件整个退出、一次都不记账。

    升级支线不许还按 30/天拦人 —— 那会让面板对着一个根本没在计数的额度说
    「今天的次数已用完」。闸关着时这里连日计数都不许读。
    """
    from app.platform import user_quota

    def _explode(*_args: Any, **_kwargs: Any):
        raise AssertionError("全局闸关着时不许读日计数")

    monkeypatch.setenv(user_quota.ENV_ENABLED, "0")
    monkeypatch.setattr(user_quota, "daily_limit", _explode)
    monkeypatch.setattr(user_quota, "used_today", _explode)
    monkeypatch.setattr(user_quota, "consume", _explode)
    _budget(monkeypatch, True)

    payload = escalation.auto_escalated_discovery_payload(
        body=_body(), session_body=_session_body(_body()), recall_result=_recall(5),
        recall_query="q", staff=STAFF,
    )
    assert payload["status"] == "queued"
    assert payload["escalation"]["quota"]["unlimited"] is True
    assert len(enqueue_spy) == 1


def test_escalation_blocked_by_daily_quota_says_so_in_plain_words(
    monkeypatch: pytest.MonkeyPatch, enqueue_spy: list[dict[str, Any]]
) -> None:
    _budget(monkeypatch, True)
    _quota(monkeypatch, limit=30, used=30)
    payload = escalation.auto_escalated_discovery_payload(
        body=_body(), session_body=_session_body(_body()), recall_result=_recall(5), recall_query="q", staff=STAFF
    )
    assert payload["status"] == "not_escalated"
    assert payload["escalation"]["reason"] == "quota_exhausted"
    assert payload["escalation"]["quota"] == {
        "limit": 30, "used": 30, "remaining": 0, "unlimited": False
    }
    assert enqueue_spy == []


def test_escalation_blocked_by_crawl_budget(
    monkeypatch: pytest.MonkeyPatch, enqueue_spy: list[dict[str, Any]]
) -> None:
    _budget(monkeypatch, False)
    _quota(monkeypatch, limit=30, used=0)
    payload = escalation.auto_escalated_discovery_payload(
        body=_body(), session_body=_session_body(_body()), recall_result=_recall(5), recall_query="q", staff=STAFF
    )
    assert payload["status"] == "not_escalated"
    assert payload["escalation"]["reason"] == "budget_exhausted"
    assert payload["escalation"]["budget"]["outcome"] == "exhausted"
    assert enqueue_spy == []


def test_a_non_budget_refusal_is_not_reported_as_a_spent_out_allowance(
    monkeypatch: pytest.MonkeyPatch, enqueue_spy: list[dict[str, Any]]
) -> None:
    """预算闸给的是 budget_decision_v1 的结构化 outcome —— 只有真花超了才敢说「用满」。"""
    _budget(monkeypatch, False, outcome="request_too_large")
    _quota(monkeypatch, limit=30, used=0)
    payload = escalation.auto_escalated_discovery_payload(
        body=_body(), session_body=_session_body(_body()), recall_result=_recall(5),
        recall_query="q", staff=STAFF,
    )
    assert payload["escalation"]["reason"] == "budget_blocked"
    assert "用满" not in payload["escalation"]["reason_human"]
    assert enqueue_spy == []


def test_unlimited_quota_passes_and_a_broken_budget_gate_fails_open(
    monkeypatch: pytest.MonkeyPatch, enqueue_spy: list[dict[str, Any]]
) -> None:
    """额度全开(<=0 = 不限)时恒放行;预算模块自己坏了也不许把搜索停掉。"""
    from app.domains.costs import budget_guard

    monkeypatch.setattr(
        budget_guard,
        "check_budget_decision",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("budget table missing")),
    )
    _quota(monkeypatch, limit=0, used=0)
    authorization = escalation.authorize_escalation(staff=STAFF)
    assert authorization.allowed is True
    assert authorization.quota["unlimited"] is True
    assert authorization.budget["checked"] is False


# ── 4b. 入队失败不许掀掉已经算好的主结果 ──
#
# 这是自动升级**自己开的**口子:改动前这条路径根本走不到入队器。/kol-smart-search 把
# RuntimeError 翻成 503、ValueError 翻成 400,而入队器抛的 ProviderJobAccessError 正好
# 继承 RuntimeError。一个「后台补人」的锦上添花,能掀掉操作员等了二十秒的二十条主结果。


def _enqueue_raises(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    def _boom(**_kwargs: Any):
        raise error

    monkeypatch.setattr(profile_discovery, "enqueue_smart_search_profile_advance", _boom)


@pytest.mark.parametrize("make_error", [
    # 围栏拒签:继承 RuntimeError → 不拦就是 503。
    lambda: __import__(
        "app.domains.kol.provider_job_access", fromlist=["ProviderJobAccessError"]
    ).ProviderJobAccessError("fence rejected for this session"),
    # 入队器自己的入参校验 → 不拦就是 400。
    lambda: ValueError("session_id must be an integer"),
    # 库故障 → 不拦就是 503。
    lambda: RuntimeError("smart search session was not created"),
])
def test_a_failed_enqueue_becomes_an_honest_refusal_not_an_exception(
    monkeypatch: pytest.MonkeyPatch, make_error
) -> None:
    _budget(monkeypatch, True)
    _quota(monkeypatch, limit=30, used=0)
    _enqueue_raises(monkeypatch, make_error())

    payload = escalation.auto_escalated_discovery_payload(
        body=_body(), session_body=_session_body(_body()), recall_result=_recall(9),
        recall_query="portrait lens creators", staff=STAFF,
    )
    assert payload["status"] == "not_escalated"
    assert payload["provider_calls_performed"] is False
    assert payload["escalation"]["escalated"] is False
    assert payload["escalation"]["reason"] == "escalation_unavailable"
    # 面板说的是人话,而且仍然把「库里只凑到 9 个」这个事实摆着。
    assert payload["escalation"]["reason_human"]
    assert payload["escalation"]["qualified_count"] == 9 and payload["escalation"]["shortfall"] == 21


# ── 5. 门面文案不许出现内部术语 ──


def test_no_internal_jargon_on_any_operator_facing_reason() -> None:
    forbidden = (
        "llm", "apify", "quota", "scope", "provider", "payload", "redis", "queue",
        "rule_v0", "lexicon", "worker", "job", "api", "配额", "队列", "作业",
    )
    for code, copy in escalation_contract._REASON_COPY.items():
        lowered = copy.lower()
        offenders = [word for word in forbidden if word in lowered]
        assert not offenders, f"{code} 的门面文案带了内部词 {offenders}: {copy}"
        assert copy.endswith(("。", "!", "?")), f"{code} 的文案不是完整一句话"


# ── 6. 已在队列时不许吹牛(9.5) ──
#
# 旧口径无条件用**本次**的 payload 覆写会话摘要。撞上在飞任务时这有两个后果:
#   * 合同被顶掉:弱的顶掉强的(面板宣传一个没人在做的 30 人在线目标的反面),
#     强的顶掉弱的(面板宣传一个正在跑的任务根本不会交付的目标)。两个方向都是撒谎。
#   * 进度被打回起点:progress.base 归 0、completion 重置,跑到一半的会话在面板上倒退。
# 现在:不插入 = 不写进度键,合同数字全部取自在飞那份 payload。

from app.domains.kol import profile_discovery_queue as _queue  # noqa: E402

_PROGRESS_KEYS = (
    "phase", "progress", "base_complete", "complete", "decision_eligible",
    "full_analysis_complete", "full_analysis_execution_complete",
    "full_analysis_observable", "requested_tasks_terminal", "required_tasks_complete",
)


def _record(monkeypatch: pytest.MonkeyPatch, *, payload: dict, running: dict, inserted: bool):
    patches: list[dict[str, Any]] = []
    monkeypatch.setattr(
        _queue.search_sessions,
        "update_session_result_summary",
        lambda _sid, *, status, summary_patch: patches.append(
            {"status": status, "patch": summary_patch}
        ),
    )
    _queue._record_smart_profile_queue(
        session_id=1234, query="q", payload=payload, smart_online_30=True,
        job={"id": 9001}, inserted=inserted, running_payload=running,
    )
    return patches[0]


_STRONG = {
    "advance_limit": 30, "advance_mode": "account_deep", "representative_video_limit": 1,
    "include_new_discovery": True, "_smart_online_30_contract": True,
}
_WEAK = {
    "advance_limit": 15, "advance_mode": "profile_only", "representative_video_limit": None,
    "include_new_discovery": False, "_smart_online_30_contract": False,
}


def test_already_queued_advertises_the_running_contract_not_the_rejected_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本次带强合同、在跑的是弱合同 → 面板只能宣传弱的那份(真正在跑的那份)。"""
    recorded = _record(monkeypatch, payload=dict(_STRONG), running=dict(_WEAK), inserted=False)
    advertised = recorded["patch"]["smart_search_profile_advance_job"]
    assert advertised["status"] == "already_queued"
    assert advertised["contract_source"] == "running_job"
    assert advertised["advance_limit"] == 15
    assert advertised["advance_mode"] == "profile_only"
    assert advertised["include_new_discovery"] is False
    # 在跑的那份不是严格在线 30 人合同,面板就不许这么说。
    assert "online_qualification" not in recorded["patch"]


def test_already_queued_does_not_reset_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = _record(monkeypatch, payload=dict(_STRONG), running=dict(_WEAK), inserted=False)
    leaked = [key for key in _PROGRESS_KEYS if key in recorded["patch"]]
    assert not leaked, f"already_queued 不该写进度键,却写了 {leaked}"
    assert set(recorded["patch"]) == {"smart_search_profile_advance_job"}


def test_already_queued_may_advertise_online_contract_only_when_running_one_has_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """反方向同样不许:本次是弱的、在跑的是强的 → 面板宣传强的那份。"""
    recorded = _record(monkeypatch, payload=dict(_WEAK), running=dict(_STRONG), inserted=False)
    advertised = recorded["patch"]["smart_search_profile_advance_job"]
    assert advertised["advance_limit"] == 30 and advertised["advance_mode"] == "account_deep"
    assert "online_qualification" in recorded["patch"]


def test_already_queued_with_unreadable_running_payload_invents_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = _record(monkeypatch, payload=dict(_STRONG), running={}, inserted=False)
    advertised = recorded["patch"]["smart_search_profile_advance_job"]
    assert advertised["contract_source"] == "unknown"
    for key in ("advance_limit", "advance_mode", "representative_video_limit", "include_new_discovery"):
        assert key not in advertised, f"读不到在跑那份时不许编 {key}"
    assert "online_qualification" not in recorded["patch"]


def test_freshly_inserted_job_still_writes_the_full_progress_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真的入了队 → 老行为一字不改(这一段是既有口径,不在本刀的改动面里)。"""
    recorded = _record(monkeypatch, payload=dict(_STRONG), running={}, inserted=True)
    assert recorded["patch"]["progress"]["total"] == 30
    assert recorded["patch"]["smart_search_profile_advance_job"]["status"] == "queued"
    assert "online_qualification" in recorded["patch"]


def test_returned_job_never_carries_the_payload_out_of_the_enqueue_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """job 会随 API 回执出站;payload 里带围栏凭证,绝不能跟着走。"""

    class _Conn:
        def execute(self, sql: str, _params: tuple):
            class _Row:
                def __init__(self, data): self._data = data
                def keys(self): return self._data.keys()
                def __iter__(self): return iter(self._data.items())
            if sql.strip().startswith("INSERT"):
                return type("R", (), {"fetchone": staticmethod(lambda: None)})()
            row = {"id": 5, "job_type": "smart_search_profile_advance", "status": "running",
                   "created_at": "t", "updated_at": "t",
                   "payload": '{"advance_limit": 30, "_provider_fence": "secret"}'}
            return type("R", (), {"fetchone": staticmethod(lambda: row)})()

        def commit(self): return None

    monkeypatch.setattr(_queue, "get_conn", lambda: _Conn())
    job, inserted, running = _queue._enqueue_smart_profile_payload({"advance_limit": 15}, 1234)
    assert inserted is False
    assert "payload" not in job and job["id"] == 5
    assert running == {"advance_limit": 30, "_provider_fence": "secret"}


# ── 7. 路由这一层:面板从「未请求」变成真实状态 ──


def _route_world(monkeypatch: pytest.MonkeyPatch, *, qualified: int) -> list[dict[str, Any]]:
    """把 /kol-smart-search 的文本腿桩到只剩「召回给出多少够格的人」这一个变量。"""
    from app.api.routers import vkpi_kol_pool_search as route

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        route.kol_smart_query_planner, "plan_text_query_provider_free",
        lambda *_a, **_k: {"status": "ready", "search_query": "camera creators"},
    )
    monkeypatch.setattr(
        route.kol_search_sessions, "ensure_session_for_result",
        lambda **_k: {"id": 4242, "status": "planned"},
    )
    monkeypatch.setattr(
        route.kol_targeted_search_runtime, "prepare_local_search",
        lambda **k: {
            "recall_filters": dict(k["recall_filters"]),
            "resolved_product": None,
            "objective": "prospective_growth",
            "local_qualification_policy": {"target_count": 30},
        },
    )
    monkeypatch.setattr(
        route.kol_targeted_search_runtime, "execute_local_search",
        lambda **_k: _recall(qualified),
    )
    monkeypatch.setattr(route.kol_profile_discovery, "filter_recall_result_platforms", lambda r, _p: r)
    monkeypatch.setattr(route.kol_profile_discovery, "filter_recall_result_market", lambda r, _m: r)
    monkeypatch.setattr(route.kol_profile_recall_qualification, "project_smart_local_result", lambda r: r)
    monkeypatch.setattr(
        route, "_attach_smart_recall_session",
        lambda **k: {**k["result"], "search_session": {"id": 4242}},
    )

    def fake_enqueue(*, query_text: str, body: dict, staff: dict | None = None, **_k: Any) -> dict:
        calls.append({"query_text": query_text, "body": body})
        return {"status": "queued", "job": {"id": 5150}}

    monkeypatch.setattr(route.kol_profile_discovery, "enqueue_smart_search_profile_advance", fake_enqueue)
    _budget(monkeypatch, True)
    _quota(monkeypatch, limit=30, used=0)
    return calls


def test_route_panel_reports_a_real_status_once_escalation_fires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from app.api.routers import vkpi_kol_pool_search as route

    calls = _route_world(monkeypatch, qualified=9)
    result = asyncio.run(
        route.smart_kol_search(
            {"input": "camera creators", "objective": "prospective_growth"}, staff=STAFF
        )
    )
    # 以前这里恒为 "not_requested" —— 面板四段永远空着,尽管库里只凑到 9 个人。
    assert result["new_discovery_status"] == "queued"
    assert result["new_discovery"]["job_id"] == 5150
    assert result["new_discovery"]["escalation"]["shortfall"] == 21
    assert result["provider_calls"] is False
    assert result["viltrox_fit_score_untouched"] is True
    # 挂在操作员正在看的那条会话上,不是新开一条。
    assert len(calls) == 1 and calls[0]["body"]["session_id"] == 4242


def test_route_keeps_the_honest_empty_state_when_the_library_already_has_enough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from app.api.routers import vkpi_kol_pool_search as route

    calls = _route_world(monkeypatch, qualified=30)
    result = asyncio.run(
        route.smart_kol_search(
            {"input": "camera creators", "objective": "prospective_growth"}, staff=STAFF
        )
    )
    assert result["new_discovery_status"] == "not_escalated"
    assert result["new_discovery"]["escalation"]["reason"] == "local_target_met"
    assert calls == []


def test_route_still_returns_the_local_results_when_the_escalation_enqueue_blows_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D2 的端到端钉:围栏拒签一次,不许把整次搜索变成 503。

    ProviderJobAccessError 继承 RuntimeError,而 /kol-smart-search 的
    ``except RuntimeError`` 会把它翻成 503 —— 操作员等了二十秒的九条结果就没了,
    换来一句「服务暂时不可用」。这条测试直接跑真路由,失败即回归。
    """
    import asyncio

    from app.api.routers import vkpi_kol_pool_search as route
    from app.domains.kol.provider_job_access import ProviderJobAccessError

    _route_world(monkeypatch, qualified=9)
    _enqueue_raises(monkeypatch, ProviderJobAccessError("fence not signed for this session"))

    result = asyncio.run(
        route.smart_kol_search(
            {"input": "camera creators", "objective": "prospective_growth"}, staff=STAFF
        )
    )
    # 主结果原封不动地交付了。
    assert len(result["result"]["items"]) == 9
    assert result["result"]["local_qualification"]["qualified_returned_count"] == 9
    # 只有「后台补人」这条腿落空,而且是人话。
    assert result["new_discovery_status"] == "not_escalated"
    assert result["new_discovery"]["escalation"]["reason"] == "escalation_unavailable"
