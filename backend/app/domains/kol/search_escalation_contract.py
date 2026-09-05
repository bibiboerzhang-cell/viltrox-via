"""搜索自动升级的**契约层**:常量、门面文案、两个判定结果的形状。零业务逻辑、零 IO。

单独成文件的理由不是行数,是**依赖方向**:前端策略表镜像、门面文案、两个 dataclass
被同族三个模块(search_escalation / _gates / _advance_body)同时用到,放进其中任何一个
都会让另外两个反向依赖它。本文件不 import 任何同族模块,所以永远不会出现环。

■ 前端策略表镜像。下面这批数字是 ``frontend/src/components/vkpi/cockpit/components/
  SmartKolInputPanel.{SearchPolicy.tsx, controller.ts, OnlineQualified.ts, LocalQualified.ts}``
  的镜像;tests/test_smart_profile_payload_equivalence.py 直接读 TS 源文件比对,防漂移。

■ 门面文案一律人话。tests/test_search_auto_escalation.py 有一条禁术语断言钉着它。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping

# 整体开关。默认开;线上要停掉自动升级只需 env + 重启,不必回滚代码。
ENV_ENABLED = "VKPI_SEARCH_AUTO_ESCALATION"

from app.domains.kol.search_platform_policy import DEFAULT_DISCOVERY_PLATFORMS, STRICT_DISCOVERY_PLATFORMS
# Supported capability is not permission to expand the default paid plan.
ONLINE_DISCOVERY_PLATFORMS: tuple[str, ...] = STRICT_DISCOVERY_PLATFORMS

QUOTA_ACTION = "smart_search_online"  # user_quota.ACTIONS 的键
APIFY_BUDGET_SCOPE = "provider:apify"
LOCAL_QUALIFICATION_SCHEMA = "smart_local_qualified_v2"  # 有它才谈得上「精准命中数」

# ── 前端策略表镜像 ──
RESULT_LIMIT = 30
CANDIDATE_LIMIT = 500
PER_PLATFORM_LIMITS: Mapping[str, int] = {"youtube": 50, "instagram": 20, "tiktok": 20}
# {策略: (creator_quota, reviewer_quota, new_discovery_limit, per_platform_limit,
#         core_vertical, expansion, exploration)}
STRATEGY_POLICY: Mapping[str, tuple[int, ...]] = {
    "vertical": (9, 21, 45, 20, 24, 5, 1),
    "balanced": (18, 12, 45, 20, 18, 9, 3),
    "expansion": (21, 9, 50, 20, 15, 12, 3),
}
DEFAULT_STRATEGY = "balanced"
MAX_POSTS = 12
REPRESENTATIVE_VIDEO_LIMIT = 1
ONLINE_QUALIFICATION_SPEC: Mapping[str, Any] = {"version": "online_net_new_30_v1", "target_count": RESULT_LIMIT}
# 只镜像 _requests_smart_local_30 真正读的两个字段(其余是前端展示用,不影响 payload)。
LOCAL_QUALIFICATION_SPEC: Mapping[str, Any] = {"version": "local_30_v1", "target_count": RESULT_LIMIT}

# ── 轴的分界:哪些键属于「召回这一次请求」,绝不许跟着进抓取轴 ──
#
# 这是本模块最容易出人命的一段。/kol-smart-search 的 body 和 profile-advance 的 body
# 有几个**同名不同轴**的键;把召回轴的值原样带进抓取轴,跑出来的是另一份合同:
#
#   mode       召回轴 = 路由开关(这次是 URL 还是文本)。抓取轴的 advance_mode 读同一个
#              名字(profile_discovery_queue._smart_profile_payload:568),不摘掉就会把
#              account_deep 换成 auto。
#   max_posts  召回轴 = **URL 预览深度**:只有 URL 分支读它(url_deep_crawl_helpers._max_posts),
#              前端对文本搜索也无条件写死 3(SmartKolInputPanel.controller.ts:436 →
#              kolPool-api.search.ts:98 无条件透传)。抓取轴 = 每个人真正抓几条,前端
#              「全网查找」腿送 12(controller.ts:699)。不摘掉 = 每次界面文字搜索触发的
#              全网抓取,每人取样都掉到 1/4。
#
# 「假值才覆盖」的补齐方式挡不住这一类:3 不是假值。所以先摘干净,再按抓取轴注入。
RECALL_ONLY_KEYS: tuple[str, ...] = (
    "create_session",       # 复用可见会话,不再开一条(前端那条腿也不带这个键)
    "mode",                 # 同名不同轴,见上
    "max_posts",            # 同名不同轴,见上
    "response_projection",  # 召回响应怎么裁剪,抓取轴没有对应物
)
# 摘掉之后必须按抓取轴真值重新注入的键(值来自前端「全网查找」腿,不是猜的)。
CRAWL_AXIS_VALUES: Mapping[str, Any] = {"max_posts": MAX_POSTS}

# 面板文案:全部人话,不出现任何内部词。
_REASON_COPY: Mapping[str, str] = {
    "disabled_by_env": "系统这段时间没有自动去全网补人,本次只给了库里已有的结果。",
    "objective_not_new_people": "本次要找的是已经有合作证据的人,不需要去全网补新人。",
    "already_requested": "本次已经带上了全网查找,不需要再自动加一次。",
    "no_local_contract": "这次没有可比对的筛选口径,系统不替你判断要不要去全网补人。",
    "no_visible_session": "本次搜索没有留下可跟进的记录,系统不会在后台替你继续找 —— 否则你看不到进度。",
    "local_target_met": "库里够格的人已经凑够,不用再去全网找。",
    "no_online_leg_for_selected_platforms": "你选的平台目前没有全网找人的通道;系统保留了你的选择,没有改去别的平台找。",
    "local_shortfall": "库里够格的人不够,已经自动接着去全网找。",
    "quota_exhausted": "你今天的全网找人次数已经用完,明天会恢复;本次只给了库里已有的结果。",
    "budget_exhausted": "本周期的抓取额度已经用满,先不自动去全网找人。",
    "budget_blocked": "抓取这一步这次没放行,系统先不自动去全网找人;在抓取额度设置里能看到具体原因。",
    # 后台那条腿没接上。本地结果已经在屏幕上了,这里只说「这一步没接上」,不许惊动主结果。
    "escalation_unavailable": "这次没能替你接上「去全网找人」这一步,本次只给了库里已有的结果;稍后再搜一次会重试。",
    "authorized": "库里够格的人不够,已经自动接着去全网找。",
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def enabled() -> bool:  # env 整体开关;运行时读,改 env 重启即生效
    return os.environ.get(ENV_ENABLED, "1").strip().lower() not in {"0", "false", "no", "off"}


def reason_human(reason_code: str) -> str:
    return _REASON_COPY.get(reason_code, "本次没有自动去全网补人。")


@dataclass(frozen=True)
class EscalationDecision:
    """第一段(纯函数)的全部产出。构造它不碰任何 IO。"""

    escalate: bool
    reason_code: str
    # False = 没有可判断的证据(或操作员自己已要了全网):面板保持空态,别拿一句
    # 「系统决定不补」去填一个本来就不该有内容的位置。
    evaluated: bool
    platforms: tuple[str, ...] = ()
    # 空 platforms + operator_selected_platforms=True = 选了但都没有联网腿。
    operator_selected_platforms: bool = False
    target_count: int = 0
    qualified_count: int = 0
    shortfall: int = 0

    @property
    def reason_human(self) -> str:
        return reason_human(self.reason_code)

    def as_panel(self) -> dict[str, Any]:  # 给面板/回执的诚实快照(无内部术语)
        return {
            "escalated": self.escalate,
            "reason": self.reason_code,
            "reason_human": self.reason_human,
            "platforms": list(self.platforms),
            "target_count": self.target_count,
            "qualified_count": self.qualified_count,
            "shortfall": self.shortfall,
        }


@dataclass(frozen=True)
class EscalationAuthorization:
    """第二段(有 IO)的产出:日配额与抓取额度这两道闸的合并结论。"""

    allowed: bool
    reason_code: str
    quota: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)

    @property
    def reason_human(self) -> str:
        return reason_human(self.reason_code)


__all__ = [
    "APIFY_BUDGET_SCOPE", "CANDIDATE_LIMIT", "CRAWL_AXIS_VALUES", "DEFAULT_STRATEGY", "DEFAULT_DISCOVERY_PLATFORMS",
    "ENV_ENABLED", "EscalationAuthorization", "EscalationDecision", "LOCAL_QUALIFICATION_SCHEMA",
    "LOCAL_QUALIFICATION_SPEC", "MAX_POSTS", "ONLINE_DISCOVERY_PLATFORMS",
    "ONLINE_QUALIFICATION_SPEC", "PER_PLATFORM_LIMITS", "QUOTA_ACTION", "RECALL_ONLY_KEYS",
    "REPRESENTATIVE_VIDEO_LIMIT", "RESULT_LIMIT", "STRATEGY_POLICY", "enabled", "reason_human",
]
