"""market_brain/tool_risk.py — 工具风险分级(C4 W10 放权能力,deploy dark)。

作用:给 agent 可调用的每个敏感操作打「风险档」low/med/high,并据档判定是否
必须人工裁决。分级依据三维(与 autonomy_license dimensions / manager_guard 口径对齐):
  1) 只读 vs 写:只读 = 低;写库 = 至少 med;
  2) 可逆性:内部可逆写 = med;外联/发布/花钱这类难撤回 = high;
  3) 资金影响:任何花钱 = high。
外部联系(发邮件/外联达人/官号发布/回评)与花钱同视作 high —— 影响不可控且难撤回。

红线(W10 开闸前一律构建不启用):
  - requires_human_approval(high)=True:high 档在人工裁决前绝不放行;
  - 未知操作按 high 兜底(fail-safe:不认识就要人工把关,绝不默认放权);
  - 本模块只做判定,绝不真执行任何动作;零触 viltrox_fit_score、零碰 rule_v0。
"""
from __future__ import annotations

from typing import Any

TIER_LOW = "low"
TIER_MED = "med"
TIER_HIGH = "high"

VALID_TIERS: frozenset[str] = frozenset({TIER_LOW, TIER_MED, TIER_HIGH})

# 未知操作的兜底档(fail-safe:不认识的动作要人工把关)。
DEFAULT_TIER = TIER_HIGH

# 敏感操作 → 风险档显式映射(口径与 gtm action_type / autonomy dimensions 对齐)。
#   low  纯只读(查询/列出/预览/汇总/观测):不改任何状态。
#   med  内部可逆写(记账/改内部状态/统计更新/入队):有留痕、可回退、不花钱、不外联。
#   high 外联 / 发布 / 花钱 / 难撤回:影响外部且不可控。
TOOL_RISK_TIERS: dict[str, str] = {
    # ── low:只读 ──
    "read": TIER_LOW,
    "query": TIER_LOW,
    "list": TIER_LOW,
    "preview": TIER_LOW,
    "summarize": TIER_LOW,
    "observe": TIER_LOW,
    "search": TIER_LOW,
    "analyze": TIER_LOW,
    "fetch_metrics": TIER_LOW,
    "score_readonly": TIER_LOW,
    # ── med:内部可逆写 ──
    "write_db": TIER_MED,
    "record_signal": TIER_MED,
    "record_outcome": TIER_MED,
    "update_arm_weight": TIER_MED,
    "enroll_kol": TIER_MED,
    "change_project_status": TIER_MED,
    "update_project_status": TIER_MED,
    "internal_note": TIER_MED,
    "queue_task": TIER_MED,
    "assign_owner": TIER_MED,
    "indie_site_update": TIER_MED,
    "landing_page_fix": TIER_MED,
    "content_retry": TIER_MED,
    # ── high:外联 / 发布 / 花钱 / 难撤回 ──
    "kol_outreach": TIER_HIGH,
    "dealer_push": TIER_HIGH,
    "official_post": TIER_HIGH,
    "review_collection": TIER_HIGH,
    "community_test": TIER_HIGH,
    "paid_boost": TIER_HIGH,
    "price_message_test": TIER_HIGH,
    "competitor_response": TIER_HIGH,
    "send_email": TIER_HIGH,
    "reply_comment": TIER_HIGH,
    "contact_external": TIER_HIGH,
    "spend_money": TIER_HIGH,
    "place_order": TIER_HIGH,
    "apify_paid_run": TIER_HIGH,
    "publish": TIER_HIGH,
}


def _normalize_key(action: str) -> str:
    return " ".join(str(action or "").split()).lower().replace("-", "_").replace(" ", "_")


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in ("1", "true", "yes", "y", "t")


def _classify_dimensions(action: dict[str, Any]) -> str:
    """按三维布尔给档:花钱/外联 → high;不可逆写 → high;可逆写 → med;只读 → low。

    识别键(缺省 False):
      spends_money / contacts_external / writes / reversible(缺省视作可逆 True)。
    """
    if _truthy(action.get("spends_money")) or _truthy(action.get("contacts_external")):
        return TIER_HIGH
    if _truthy(action.get("writes")):
        # reversible 未给按可逆处理(True);显式不可逆 → high。
        reversible = action.get("reversible", True)
        return TIER_MED if _truthy(reversible) else TIER_HIGH
    return TIER_LOW


def classify_action(action: Any) -> str:
    """把一个操作(操作名字符串或三维布尔 dict)映射到 low/med/high 风险档。

    - dict:按 _classify_dimensions 三维判定(资金/外联/可逆性)。
    - str :查 TOOL_RISK_TIERS;未知 → DEFAULT_TIER(high 兜底,fail-safe)。
    纯函数,零副作用。
    """
    if isinstance(action, dict):
        return _classify_dimensions(action)
    return TOOL_RISK_TIERS.get(_normalize_key(str(action)), DEFAULT_TIER)


def requires_human_approval(tier: Any) -> bool:
    """high 档必须人工裁决前放行 → True;low/med → False;非法档按 high 兜底 → True。"""
    normalized = str(tier or "").strip().lower()
    if normalized in VALID_TIERS:
        return normalized == TIER_HIGH
    # 非法/未知档不敢放行,按最严处理。
    return True
