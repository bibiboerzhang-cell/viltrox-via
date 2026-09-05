"""搜索自动升级:库里没凑够人时,替操作员把「去全网继续找」这一步接上。

病根:搜一次只跑库内召回就返回。够格的人不足 30 个时链路停在这里 —— 会话上挂不到
任何抓取任务,进度面板四段永远是「未请求」,人得自己再点一次才会真去平台上找人。

■ 两段求值(顺序是硬约束)。第一段 :func:`decide_escalation` 是**纯函数、零 IO**,
  只看请求体与本次召回结果;不升级的纯库内搜索在这一段就返回,因此**零新增
  DB / Redis 往返**(test_search_auto_escalation 用会爆炸的假 conn/假 redis 钉死)。
  第二段 ``search_escalation_gates.authorize_escalation`` 只有判定要升级才调用。

■ 平台选择是操作员的(9.4)。``discovery_filters._platforms`` 的口径是「解析不出支持
  的平台就兜底三平台」,升级路径不能用它:只勾了 Facebook(暂无严格联网发现腿)时,
  兜底会把搜索悄悄改成 YouTube/Instagram/TikTok。:func:`operator_online_platforms`
  因此区分两种空 —— 没选过(用默认三平台)与选了但都没有联网腿(不升级,并说人话)。

■ 入队失败绝不外抛(见 :func:`_enqueue_escalation`)。升级是「后台补人」的锦上添花,
  掀不得操作员已经等了二十秒的主结果。

■ 这条支线**不记配额**(理由见 :func:`auto_escalated_discovery_payload` 的注释)。

同族分工:
  ``search_escalation_contract``     常量 / 门面文案 / 两个结果 dataclass(零同族依赖)
  ``search_escalation_gates``        日配额闸 + 抓取额度闸
  ``search_escalation_advance_body`` payload 等价化(9.3),含召回轴/抓取轴的分界

红线:不写 viltrox_fit_score、不碰 rule_v0、请求侧不调用任何 provider/LLM。
"""
from __future__ import annotations

from typing import Any, Mapping

from app.core.logging import get_logger
from app.domains.kol import profile_discovery, search_sessions_online, targeted_search_contract
from app.domains.kol.discovery_filters import _int, _text
from app.domains.kol.search_escalation_advance_body import escalation_advance_body
from app.domains.kol.search_escalation_contract import (
    CANDIDATE_LIMIT,
    DEFAULT_DISCOVERY_PLATFORMS,
    DEFAULT_STRATEGY,
    ENV_ENABLED,
    LOCAL_QUALIFICATION_SCHEMA,
    LOCAL_QUALIFICATION_SPEC,
    MAX_POSTS,
    ONLINE_DISCOVERY_PLATFORMS,
    ONLINE_QUALIFICATION_SPEC,
    PER_PLATFORM_LIMITS,
    QUOTA_ACTION,
    RECALL_ONLY_KEYS,
    REPRESENTATIVE_VIDEO_LIMIT,
    RESULT_LIMIT,
    STRATEGY_POLICY,
    EscalationAuthorization,
    EscalationDecision,
    _mapping,
    enabled,
    reason_human,
)
from app.domains.kol.search_escalation_gates import authorize_escalation

logger = get_logger(__name__)

# 操作员自己已经要了全网查找的三种说法(任一为真就走既有分支,不再自动加一次)。
_ALREADY_REQUESTED_FLAGS = ("include_new_discovery", "include_discovery", "execute_new_discovery")
_OBJECTIVE_KEYS = ("objective", "search_objective", "searchObjective")
_SESSION_ID_KEYS = ("session_id", "search_session_id")
_EMPTY_CHOICE = (None, "", [], ())


# ── 平台:操作员选的那一份(9.4) ──


def _raw_platform_choice(body: Mapping[str, Any]) -> Any:
    """按前端可能用过的四个键名取平台选择;顶层都没有再看 filters。取不到返回 None。"""
    for key in ("new_discovery_platforms", "discovery_platforms", "platforms", "platform"):
        value = body.get(key)
        if value not in _EMPTY_CHOICE:
            return value
    value = _mapping(body.get("filters")).get("platforms")
    return value if value not in _EMPTY_CHOICE else None


def _named_platforms(raw: Any) -> list[str]:
    """标准化成小写平台名;``all`` / ``*`` 这类「没选」的写法在这里就被丢掉。"""
    values = raw if isinstance(raw, (list, tuple, set)) else [raw]
    return [
        {"twitter": "x"}.get(_text(item).lower(), _text(item).lower())
        for item in values
        if _text(item) and _text(item).lower() not in {"all", "*"}
    ]


def operator_online_platforms(body: Mapping[str, Any]) -> tuple[tuple[str, ...], bool]:
    """返回 (可用于全网找人的平台, 操作员是否显式选过平台)。9.4 的落点。

    **绝不**把「选了但都不支持」扩成默认三平台 —— 那是拿系统偏好顶掉操作员的选择。
    """
    requested = _named_platforms(_raw_platform_choice(_mapping(body)))
    if not requested:
        return DEFAULT_DISCOVERY_PLATFORMS, False
    kept = tuple(dict.fromkeys(name for name in requested if name in ONLINE_DISCOVERY_PLATFORMS))
    return kept, True


# ── 第一段:纯函数 ──


def _local_counts(recall_result: Mapping[str, Any]) -> tuple[bool, int, int, int]:
    """(有无命名口径, 目标数, 精准命中数, 缺口)。没有命名口径就没有可信的命中数。"""
    contract = _mapping(recall_result.get("local_qualification"))
    if _text(contract.get("schema")) != LOCAL_QUALIFICATION_SCHEMA:
        return False, 0, 0, 0
    policy = _mapping(contract.get("policy"))
    target = _int(policy.get("target_count"), 0) or RESULT_LIMIT
    key = "qualified_returned_count" if "qualified_returned_count" in contract else "qualified_count"
    qualified = _int(contract.get(key), 0)
    shortfall = _int(contract.get("shortfall"), max(0, target - qualified))
    return True, target, qualified, max(0, shortfall)


def _states_new_people_objective(body: Mapping[str, Any]) -> bool:
    """「找新人」必须是操作员**明说**的。

    normalize_objective 的兜底值也是 prospective_growth —— 拿兜底当意图就是把默认值读成
    人的选择,缺席不是证据。前端两条腿都显式带 objective,所以这一条不影响真实 UI。
    """
    stated = next((body[key] for key in _OBJECTIVE_KEYS if _text(body.get(key))), None)
    return stated is not None and (
        targeted_search_contract.normalize_objective(stated)
        == targeted_search_contract.PROSPECTIVE_GROWTH
    )


def _precheck_reason(body: Mapping[str, Any], visible_session_id: int | None) -> str | None:
    """「本来就不该问」的三道。返回原因码 = 到此为止;返回 None = 可以继续往下判。

    没有可见会话(create_session=false 或会话没建成)就不许升级:升级的全部意义是
    「人能在面板上看着它跑完」,没会话就没面板,后台却真在花钱抓 —— 那是替人做主
    又不给他看;顺带杜绝自动路径偷偷新建会话。
    """
    if not visible_session_id:
        return "no_visible_session"
    if any(body.get(flag) for flag in _ALREADY_REQUESTED_FLAGS):
        return "already_requested"
    if not _states_new_people_objective(body):
        return "objective_not_new_people"
    return None


def _shortfall_verdict(
    *, platforms: tuple[str, ...], operator_selected: bool, target: int, qualified: int, shortfall: int,
) -> tuple[bool, str, bool]:
    """(要不要升级, 原因码, 结论里保不保留平台列表)。有了命中数之后的最后三问。"""
    if not enabled():
        return False, "disabled_by_env", True
    if qualified >= target or shortfall <= 0:
        return False, "local_target_met", True
    if operator_selected and not platforms:
        # 只勾了没有联网腿的平台(例如 Facebook)。保住这个选择,什么都不改。
        return False, "no_online_leg_for_selected_platforms", False
    return True, "local_shortfall", True


def decide_escalation(
    *, body: Mapping[str, Any], recall_result: Mapping[str, Any], visible_session_id: int | None = None,
) -> EscalationDecision:
    """要不要去全网补人。**纯函数:零 DB、零 Redis、零 provider。**

    判定顺序从「本来就不该问」走到「真的缺人」,每一步给一个封闭原因码。
    ``visible_session_id`` = 操作员这一刻正在看的那条会话;没有它就不许升级。
    """
    body = _mapping(body)
    blocked = _precheck_reason(body, visible_session_id)
    if blocked:
        return EscalationDecision(False, blocked, evaluated=False)

    # 没跑命名筛选口径 = 没有「精准命中数」这个量。此时判不出缺口,也就不许替人做主。
    has_contract, target, qualified, shortfall = _local_counts(_mapping(recall_result))
    if not has_contract:
        return EscalationDecision(False, "no_local_contract", evaluated=False)

    platforms, operator_selected = operator_online_platforms(body)
    escalate, code, keep_platforms = _shortfall_verdict(
        platforms=platforms, operator_selected=operator_selected,
        target=target, qualified=qualified, shortfall=shortfall,
    )
    return EscalationDecision(
        escalate, code, evaluated=True,
        platforms=platforms if keep_platforms else (),
        operator_selected_platforms=operator_selected,
        target_count=target, qualified_count=qualified, shortfall=shortfall,
    )


# ── 出口一:操作员**自己**要了全网发现(既有分支,行为逐字不变) ──


def _requested_strict_online_30(body: Mapping[str, Any]) -> bool:
    online_spec = body.get("online_qualification_spec")
    return bool(
        isinstance(online_spec, dict)
        and str(online_spec.get("version") or "") == "online_net_new_30_v1"
        and str(online_spec.get("target_count") or "") == "30"
    )


def _requested_discovery_limit(body: Mapping[str, Any]) -> int:
    return int(body.get("new_discovery_limit") or body.get("discovery_limit") or 15)


def _requested_discovery_platforms(body: Mapping[str, Any], explicit_platforms: Any) -> Any:
    return (
        body.get("new_discovery_platforms")
        or body.get("discovery_platforms")
        or body.get("platforms")
        or explicit_platforms
    )


def _requested_enqueue_payload(
    *, body: Mapping[str, Any], recall_query: str,
    staff: Mapping[str, Any] | None, discovery_platforms: Any,
) -> dict[str, Any]:
    """冻结口径:这里交给入队的是 ``body`` 本身(不是 session_body),不在本刀里改。"""
    queued = profile_discovery.enqueue_smart_search_profile_advance(
        query_text=recall_query,
        body={
            **body,
            "original_query_text": recall_query,
            "include_new_discovery": True,
            "new_discovery_limit": _requested_discovery_limit(body),
            "new_discovery_platforms": discovery_platforms,
            "platform": str(body.get("platform") or ""),
        },
        staff=staff,
    )
    status = queued.get("status") or "queued"
    return {
        "status": status,
        "deferred_to_queue": True,
        "job_id": queued.get("job_id") or (queued.get("job") or {}).get("id"),
        "progressive": True,
        "provider_calls_performed": False,
        **({"online_qualification": search_sessions_online.queued_online_qualification(status)}
           if _requested_strict_online_30(body) else {}),
    }


def requested_discovery_payload(
    *, body: Mapping[str, Any], recall_query: str, effective_query: str,
    explicit_platforms: Any, staff: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """返回 None = 操作员没要全网发现,轮到自动升级那条出口去判。"""
    if not bool(body.get("include_new_discovery") or body.get("include_discovery")):
        return None
    discovery_platforms = _requested_discovery_platforms(body, explicit_platforms)
    if not bool(body.get("execute_new_discovery")):
        return profile_discovery.discovery_plan(
            query_text=effective_query,
            platforms=discovery_platforms,
            platform_hint=str(body.get("platform") or ""),
            limit=_requested_discovery_limit(body),
        )
    return _requested_enqueue_payload(
        body=body, recall_query=recall_query, staff=staff, discovery_platforms=discovery_platforms,
    )


# ── 出口二:操作员没要,但库里没凑够 ──


def _visible_session_id(session_body: Mapping[str, Any]) -> int | None:
    visible = _mapping(session_body)
    return next(
        (_int(visible[key], 0) or None for key in _SESSION_ID_KEYS if visible.get(key) not in (None, "")),
        None,
    )


def _refusal_payload(
    decision: EscalationDecision, *,
    authorization: EscalationAuthorization | None = None, reason_code: str | None = None,
) -> dict[str, Any]:
    """判得出来但没升级时的回执:人话原因 + 两份额度快照,没有内部术语。"""
    panel = {**decision.as_panel(), "escalated": False}
    code = reason_code or (authorization.reason_code if authorization else "")
    if code:
        panel.update({"reason": code, "reason_human": reason_human(code)})
    if authorization is not None:
        panel.update({"quota": authorization.quota, "budget": authorization.budget})
    return {"status": "not_escalated", "provider_calls_performed": False, "escalation": panel}


def _enqueue_escalation(
    *, body: Mapping[str, Any], decision: EscalationDecision, recall_query: str,
    session_id: int | None, staff: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    """入队;失败返回 None。**任何异常都不许外抛。**

    升级是「后台补人」的锦上添花,而调用方 /kol-smart-search 把 RuntimeError 翻成 503、
    ValueError 翻成 400 —— 入队器抛的 ProviderJobAccessError 正好继承 RuntimeError,
    入队期还可能抛 ValueError 或库故障。不拦在这里,一次补人失败就能掀掉操作员已经等了
    二十秒、已经算好的那二十条主结果。改动前这条路径根本走不到入队器,这个口子是自动
    升级自己开的,得自己堵上。
    """
    try:
        return profile_discovery.enqueue_smart_search_profile_advance(
            query_text=recall_query,
            body=escalation_advance_body(
                body, platforms=decision.platforms, query_text=recall_query, session_id=session_id
            ),
            staff=staff,
        )
    except Exception as exc:  # noqa: BLE001 —— 故意兜底:主结果比这条后台腿重要
        logger.warning(
            "search_escalation.enqueue_failed error=%s session_id=%s",
            type(exc).__name__, session_id, exc_info=True,
        )
        return None


def _queued_payload(
    queued: Mapping[str, Any], decision: EscalationDecision, authorization: EscalationAuthorization,
) -> dict[str, Any]:
    status = _text(queued.get("status")) or "queued"
    return {
        "status": status,
        "deferred_to_queue": True,
        "job_id": queued.get("job_id") or _mapping(queued.get("job")).get("id"),
        "progressive": True,
        "provider_calls_performed": False,
        "online_qualification": search_sessions_online.queued_online_qualification(status),
        "escalation": {
            **decision.as_panel(),
            "quota": authorization.quota,
            "budget": authorization.budget,
        },
    }


def auto_escalated_discovery_payload(
    *, body: Mapping[str, Any], session_body: Mapping[str, Any],
    recall_result: Mapping[str, Any], recall_query: str, staff: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """操作员没要,但库里没凑够 —— 两段求值后决定要不要替他接上全网这条腿。

    返回 None = 本次根本没有可说的(面板保持诚实空态)。

    **这条路径不扣次数,是想清楚的,不是漏了。** 中间件按 body 旗标记账:
    ``/kol-smart-search`` 要的那三个旗标这条路径上一个都没有,所以本次请求不记;但前端
    每做一次文字搜索都必发 ``/kol-smart-search/profile-advance-job``(controller.ts:540),
    而那个端点在 ``user_quota._ROUTE_RULES`` 里是**空旗标 = 每个 POST 都记一笔**。两条腿
    撞的是同一个幂等键、同一份工作,这里再自扣一笔就是双记 —— 操作员的 30 次/天当场腰斩
    成 15 次,连什么新工作都没产生的 already_queued 也照扣。升级**之前**的次数检查照旧
    留着(``search_escalation_gates.authorize_escalation``),所以真用完时这条腿仍然停。
    代价:纯 API 客户端只打 /kol-smart-search、从不打 advance 端点时会少记一笔 ——
    少记一笔远好过把所有人的日上限砍一半。
    """
    session_id = _visible_session_id(session_body)
    decision = decide_escalation(body=body, recall_result=recall_result, visible_session_id=session_id)
    if not decision.escalate:
        # evaluated=False 的几种情况没有任何可宣传的东西,继续保持「未请求」空态。
        if not decision.evaluated:
            return None
        return _refusal_payload(decision)

    authorization = authorize_escalation(staff=staff)
    if not authorization.allowed:
        return _refusal_payload(decision, authorization=authorization)

    # 会话绑定:挂在操作员正在看的那条会话上(session_id 在上面已经算好并且非空)。
    queued = _enqueue_escalation(
        body=body, decision=decision, recall_query=recall_query, session_id=session_id, staff=staff,
    )
    if queued is None:
        return _refusal_payload(
            decision, authorization=authorization, reason_code="escalation_unavailable"
        )
    return _queued_payload(_mapping(queued), decision, authorization)


__all__ = [
    "CANDIDATE_LIMIT", "DEFAULT_STRATEGY", "DEFAULT_DISCOVERY_PLATFORMS", "ENV_ENABLED", "LOCAL_QUALIFICATION_SCHEMA",
    "LOCAL_QUALIFICATION_SPEC", "MAX_POSTS", "ONLINE_DISCOVERY_PLATFORMS",
    "ONLINE_QUALIFICATION_SPEC", "PER_PLATFORM_LIMITS", "QUOTA_ACTION", "RECALL_ONLY_KEYS",
    "REPRESENTATIVE_VIDEO_LIMIT", "RESULT_LIMIT", "STRATEGY_POLICY",
    "EscalationAuthorization", "EscalationDecision", "authorize_escalation",
    "auto_escalated_discovery_payload", "decide_escalation", "enabled", "escalation_advance_body",
    "operator_online_platforms", "reason_human", "requested_discovery_payload",
]
