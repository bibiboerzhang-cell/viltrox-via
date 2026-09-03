"""自动升级的第二段:日配额闸与抓取额度闸。**只有第一段判定要升级才跑**(这里有真 IO)。

■ 两道闸各说各的真话。额度全开(限额 <= 0 = 不限)恒放行;被拦下时给人话原因,不是错误码。
  预算侧只有 ``budget_decision_v1`` 的 outcome 真是 ``exhausted`` 才敢说「额度用满」——
  别的拒绝原因另有说法(见 vkpi-video-budget-gate 复盘:拿「没配置」当「花光了」会误杀)。

■ 全局闸关掉时这里必须跟着退场(⑦-1)。``user_quota.quota_enabled()`` 为假时,中间件
  整个 return None、一次都不记账;升级支线若还按 30/天拦人,面板会对着一个根本没在计数的
  额度说「今天的次数已用完」—— 两处口径打架,而且是升级支线单方面在撒谎。

■ 这里**不记账**。为什么见 search_escalation.auto_escalated_discovery_payload 的注释:
  同一份工作由前端必发的 advance 端点的中间件记一笔,这里再记就是双记。
"""
from __future__ import annotations

from typing import Any, Mapping

from app.core.logging import get_logger
from app.domains.kol.discovery_filters import _text
from app.domains.kol.search_escalation_contract import (
    APIFY_BUDGET_SCOPE,
    QUOTA_ACTION,
    EscalationAuthorization,
    _mapping,
)

logger = get_logger(__name__)

# 闸不生效时的诚实快照:没有限额,也就没有「用了几次」可说。
_UNLIMITED_QUOTA: Mapping[str, Any] = {"limit": 0, "used": 0, "unlimited": True, "remaining": None}


def _staff_quota_id(staff: Mapping[str, Any] | None) -> int:
    """与 user_quota._staff_id_from_request 逐字同序 —— 必须落进同一个计数桶。"""
    staff = _mapping(staff)
    try:
        return int(staff.get("id") or staff.get("staff_id") or staff.get("user_id") or 0)
    except (TypeError, ValueError):
        return 0


def _quota_snapshot(staff: Mapping[str, Any] | None) -> tuple[dict[str, Any], bool]:
    """(给面板看的额度快照, 是否已用完)。全局闸关着时直接按「不限」返回,一次都不读计数。"""
    from app.platform import user_quota

    if not user_quota.quota_enabled():
        return dict(_UNLIMITED_QUOTA), False
    staff_id = _staff_quota_id(staff)
    limit = user_quota.daily_limit(QUOTA_ACTION)
    metered = staff_id > 0 and limit > 0
    used = user_quota.used_today(QUOTA_ACTION, staff_id) if metered else 0
    quota = {
        "limit": limit,
        "used": used,
        "unlimited": limit <= 0,
        "remaining": None if limit <= 0 else max(0, limit - used),
    }
    return quota, bool(metered and used >= limit)


def _budget_snapshot() -> tuple[dict[str, Any], str | None]:
    """(给面板看的额度快照, 拦下的原因码或 None)。闸自己坏了要放行,不许因此停掉搜索。"""
    try:
        from app.domains.costs import budget_guard

        # require_configured=False 是刻意的:没配过预算不等于「花光了」,拿没配置当耗尽
        # 会把正常搜索误杀(见 vkpi-video-budget-gate 复盘)。
        decision = budget_guard.check_budget_decision(APIFY_BUDGET_SCOPE, 0.0, require_configured=False)
        structured = decision if isinstance(decision, Mapping) else {}
        allowed = bool(structured.get("allowed", True)) if structured else bool(decision)
        outcome = _text(structured.get("outcome"))
        budget = {"scope": APIFY_BUDGET_SCOPE, "checked": True, "allowed": allowed, "outcome": outcome}
    except Exception:
        # 预算模块读不出来时方向安全:放行 + 记一笔,绝不因为闸自己坏了就停掉搜索。
        logger.warning("search_escalation.budget_check_unavailable", exc_info=True)
        return {"scope": APIFY_BUDGET_SCOPE, "checked": False, "allowed": True}, None
    if allowed:
        return budget, None
    # 只有真的花超了才敢说「额度用满」;别的拒绝原因各说各的(budget_decision_v1)。
    return budget, ("budget_exhausted" if outcome == "exhausted" else "budget_blocked")


def authorize_escalation(*, staff: Mapping[str, Any] | None) -> EscalationAuthorization:
    """两道闸合并成一个结论。放行与否都带上两份快照,面板不必再去猜。"""
    quota, exhausted = _quota_snapshot(staff)
    if exhausted:
        return EscalationAuthorization(False, "quota_exhausted", quota=quota)
    budget, blocked = _budget_snapshot()
    if blocked:
        return EscalationAuthorization(False, blocked, quota=quota, budget=budget)
    return EscalationAuthorization(True, "authorized", quota=quota, budget=budget)


__all__ = ["authorize_escalation"]
