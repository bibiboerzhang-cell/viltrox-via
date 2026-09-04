"""搜索松绑口径:「把找到的最好的人给出来,并诚实标注凭什么」。

2026-08 的三道闸(证据门 AND-2 / 全局排除已关注 / 视频 45 天硬拒)把口径从「过滤」变成了
「清零」:14 条真实历史查询全量重放,精准命中 0/14,而六七月是 17.2 / 13.5。本模块把那三道
闸从**硬杀**降级成**排序惩罚 + 诚实标注**,并且只降这三道 —— 操作员显式勾选的平台 / 国家 /
语言 / 粉丝量 / 器材内容,以及地区规避(CN/HK/TW)、自有账号 / 品牌官方 / 零售 / 无效账号 /
跨来源重复,**一律仍然硬拒**,只是要记进诚实的缺口账。

一个开关退回八月严口径::

    请求体 {"strict_gates": true}   →  local_qualification_policy["gate_mode"] = "strict"

严口径下三项逐字回到 2026-09-03 的行为:收藏全局隐藏、视频 45 天硬拒、意图腿要 2 个证据。
另有一个独立开关 ``hide_team_favorites``:松绑模式下默认 False(显示 + 标注),操作员想把
同事关注过的人藏起来时单独打开,不必连带收紧另外两道闸。

本模块**纯计算**:零 IO、零 LLM、零数据库、不写 ``viltrox_fit_score``、不碰 rule_v0、不新造
评分。排序惩罚一律复用既有 ``ranking_key`` / ``_score_key`` 的分区位,只决定「谁排在谁后面」。
"""
from __future__ import annotations

from typing import Any


RELAXATION_SCHEMA = "search_relaxation_v1"

#: 口径住在 ``local_qualification_policy`` 里,随请求一路传到资质门与回填梯。
POLICY_KEY = "gate_mode"
MODE_RELAXED = "relaxed"
MODE_STRICT = "strict"
MODES: tuple[str, ...] = (MODE_RELAXED, MODE_STRICT)
#: 产品默认:松绑。严口径必须由请求显式点名。
DEFAULT_MODE = MODE_RELAXED

#: 请求体开关(布尔)。``strict_gates=true`` 一键退回八月严口径。
BODY_STRICT_KEY = "strict_gates"
#: 请求体开关(布尔)。松绑模式下把「同事已关注」的人重新藏起来,不影响另外两道闸。
BODY_HIDE_FAVORITES_KEY = "hide_team_favorites"
#: 上面这个开关在策略字典里的落点。
HIDE_FAVORITES_POLICY_KEY = "hide_team_favorites"

#: 严口径的视频年龄上限(2026-08-15 起的既有常量口径)。
STRICT_MAX_VIDEO_AGE_DAYS = 45
#: 松绑后的上限。库内普查:≤45 天的人全库只有 5 个(0.28%),≤365 天有 107 个;再往上放到
#: 730 天只多 1.1 人/查询,收益已被证据门吃住,所以停在 365。
RELAXED_MAX_VIDEO_AGE_DAYS = 365

#: 意图腿要求候选证明几个查询词。AND-2 的前提是候选行有 8 个可举证字段,而本地池
#: content_style 填充率 0/1809,profile_text 与 type_reason 两列在表里根本不存在——
#: 八个字段里三个恒空。在线腿 2026-08-25 已因同一理由降到 1,本地腿在松绑模式下同口径。
#: 产品腿(型号 / 品牌 / 卡口 / 画幅)一个字不动。
STRICT_MIN_INTENT_TERMS = 2
RELAXED_MIN_INTENT_TERMS = 1

#: 松绑模式下从「硬拒」降级为「可回填 + 卡面标注」的资质门原因码。
#: 只有这一个 —— 视频陈旧量的是我们的抓取跟进度,不是创作者的活跃度。
RELAXABLE_QUALIFICATION_REASONS: frozenset[str] = frozenset({"latest_video_stale"})

#: 无论什么模式都**永不放宽**的判据(写在这里是为了让「松绑」的边界可读、可测)。
NEVER_RELAXED: tuple[str, ...] = (
    "platforms",
    "countries",
    "languages",
    "followers_min",
    "followers_max",
    "gear_content",
    "excluded_region",
    "account_own_brand",
    "account_brand_official",
    "account_retailer",
    "account_garbage",
    "duplicate_canonical_identity",
)

#: 卡面文案(门面只说人话,不出现任何内部术语)。
TEAM_FAVORITE_NOTE = "已被同事关注"
STALE_ACTIVITY_NOTE = "近期没有更新视频"


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _flag(value: Any) -> bool:
    """把请求体里可能的 true / "true" / 1 统一读成布尔;读不懂一律当没点名。"""

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value) == 1
    return str(value or "").strip().lower() in {"true", "yes", "on", "1"}


def normalize_mode(value: Any) -> str:
    """把任意输入收敛成两个合法口径之一;不认识的值一律回到默认(松绑)。"""

    mode = str(value or "").strip().lower()
    return mode if mode in MODES else DEFAULT_MODE


def resolve_mode(body: Any) -> str:
    """请求体 → 口径。``strict_gates`` 为真才收紧;缺省 / 读不懂都是松绑。"""

    payload = _mapping(body)
    if _flag(payload.get(BODY_STRICT_KEY)):
        return MODE_STRICT
    return normalize_mode(payload.get(POLICY_KEY) or DEFAULT_MODE)


def resolve_hide_team_favorites(body: Any, *, mode: str) -> bool:
    """严口径恒隐藏;松绑模式下只有操作员点名才隐藏。"""

    if normalize_mode(mode) == MODE_STRICT:
        return True
    return _flag(_mapping(body).get(BODY_HIDE_FAVORITES_KEY))


def policy_mode(policy: Any) -> str:
    """读出这份策略的口径。**没写就是严口径。**

    松绑必须有据可查地写在策略里(``smart_local_policy`` 总会写)。没有策略字典、或者
    字典里根本没有这个键,说明这条车道从来没有接入松绑合同 —— 那就一个字节都不改它的
    行为,而不是替它做主放宽。八月那批闸的病根正是「默认悄悄变了」,不能在这里重犯。
    """

    spec = _mapping(policy)
    if POLICY_KEY not in spec:
        return MODE_STRICT
    return normalize_mode(spec.get(POLICY_KEY))


def is_relaxed(policy: Any) -> bool:
    return policy_mode(policy) == MODE_RELAXED


def hide_team_favorites(policy: Any) -> bool:
    """本次是否把「同事已关注」的人从召回里藏起来。松绑模式缺省 False。"""

    spec = _mapping(policy)
    if HIDE_FAVORITES_POLICY_KEY in spec:
        return _flag(spec.get(HIDE_FAVORITES_POLICY_KEY))
    return policy_mode(policy) == MODE_STRICT


def max_video_age_days(mode: Any) -> int:
    """该口径下的视频年龄上限(天)。"""

    return (
        RELAXED_MAX_VIDEO_AGE_DAYS
        if normalize_mode(mode) == MODE_RELAXED
        else STRICT_MAX_VIDEO_AGE_DAYS
    )


def effective_max_video_age_days(policy: Any, *, fallback: int) -> int:
    """读策略里已写好的上限;缺失 / 非法时回落到调用方给的既有常量。"""

    raw = _mapping(policy).get("max_video_age_days")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return int(fallback)
    return value if value > 0 else int(fallback)


def min_intent_terms(policy: Any) -> int:
    return RELAXED_MIN_INTENT_TERMS if is_relaxed(policy) else STRICT_MIN_INTENT_TERMS


def relaxable_reasons(policy: Any) -> frozenset[str]:
    """本次可以从硬拒降级成「回填 + 标注」的资质门原因码。"""

    return RELAXABLE_QUALIFICATION_REASONS if is_relaxed(policy) else frozenset()


# ── 卡面标注(只说人话) ───────────────────────────────────────────────────


def _append_note(item: dict[str, Any], note: str) -> None:
    notes = [str(value) for value in (item.get("selection_notes") or ()) if str(value)]
    if note not in notes:
        notes.append(note)
    item["selection_notes"] = notes


def annotate_team_favorite(item: Any) -> Any:
    """标注「已被同事关注」。不隐藏、不扣分、不改归属,只是让操作员一眼看见。"""

    if not isinstance(item, dict):
        return item
    item["team_favorite"] = True
    item["team_favorite_note"] = TEAM_FAVORITE_NOTE
    _append_note(item, TEAM_FAVORITE_NOTE)
    return item


def annotate_if_team_favorite(item: Any, kol_pool_id: Any, favorited_ids: Any) -> Any:
    """召回段已经认出来的人在投影段盖章。隐藏口径下 ``favorited_ids`` 恒为空集。"""

    if kol_pool_id in (favorited_ids or ()):
        return annotate_team_favorite(item)
    return item


def is_team_favorite(item: Any) -> bool:
    return isinstance(item, dict) and item.get("team_favorite") is True


def activity_age_days(item: Any) -> float | None:
    """从既有过闸证明里读出最新视频的天数;读不到返回 None(= 我们从没抓过)。"""

    gate = _mapping(_mapping(item).get("qualification_evidence"))
    activity = _mapping(gate.get("activity"))
    try:
        value = float(activity.get("age_days"))
    except (TypeError, ValueError):
        return None
    return value


def annotate_stale_activity(item: Any, *, strict_window: int = STRICT_MAX_VIDEO_AGE_DAYS) -> Any:
    """只在严口径会判死、松绑放行的那批人身上盖「近期没有更新视频」。"""

    if not isinstance(item, dict):
        return item
    age = activity_age_days(item)
    if age is None or age <= float(strict_window):
        return item
    item["activity_recency_note"] = STALE_ACTIVITY_NOTE
    _append_note(item, STALE_ACTIVITY_NOTE)
    return item


def annotate_stale_activity_all(
    items: Any,
    *,
    strict_window: int = STRICT_MAX_VIDEO_AGE_DAYS,
) -> Any:
    """给一批过闸的人逐个盖「近期没有更新视频」(严口径下没人会被盖到)。"""

    for item in items or ():
        annotate_stale_activity(item, strict_window=strict_window)
    return items


# ── 召回层:收藏是隐藏还是标注 ────────────────────────────────────────────


def apply_favorite_policy(
    considered: list[Any],
    survivors: list[Any],
    exclusion: dict[str, Any],
    *,
    hide: bool,
) -> dict[str, Any]:
    """决定「同事已关注」的人是被隐藏还是留在结果里带标注。

    隐藏(严口径 / 操作员点名)时逐字保持既有行为:survivors 进主跑,被摘的人交给回填梯的
    第一级。标注(松绑默认)时全部留在主跑里参与排序,``favorited_hits`` 清空 —— 他们已经
    在场,再走回填梯就会重复占位。两条路的诊断都如实计数,口径写在 ``mode`` 里。
    """

    survivor_ids = {id(hit) for hit in survivors}
    favorited = [hit for hit in considered if id(hit) not in survivor_ids]
    block = dict(exclusion or {})
    if hide:
        block["mode"] = "hidden"
        block["annotated_count"] = 0
        return {
            "hits": list(survivors),
            "favorited_hits": favorited,
            "favorited_ids": set(),
            "exclusion": block,
        }
    block["mode"] = "annotated"
    block["annotated_count"] = int(block.get("excluded_count") or 0)
    block["excluded_count"] = 0
    block["excluded_ids"] = []
    block["annotated_note"] = TEAM_FAVORITE_NOTE
    return {
        "hits": list(considered),
        "favorited_hits": [],
        "favorited_ids": {
            getattr(hit, "kol_pool_id", None)
            for hit in favorited
            if getattr(hit, "kol_pool_id", None) is not None
        },
        "exclusion": block,
    }


# ── 回执:键名、默认值、本次取值 ──────────────────────────────────────────


def relaxation_receipt(policy: Any) -> dict[str, Any]:
    """诊断块里的松绑回执:一眼看清「开关叫什么、默认是什么、这次是什么」。"""

    mode = policy_mode(policy)
    relaxed = mode == MODE_RELAXED
    return {
        "schema": RELAXATION_SCHEMA,
        "mode": mode,
        "default_mode": DEFAULT_MODE,
        "strict_switch": {
            "body_key": BODY_STRICT_KEY,
            "default": False,
            "value": not relaxed,
            "effect": "回到 2026-08 严口径:收藏隐藏 + 视频 45 天硬拒 + 意图腿要 2 个证据",
        },
        "hide_team_favorites": {
            "body_key": BODY_HIDE_FAVORITES_KEY,
            "policy_key": HIDE_FAVORITES_POLICY_KEY,
            "default": False,
            "value": hide_team_favorites(policy),
        },
        "max_video_age_days": {
            "policy_key": "max_video_age_days",
            "strict": STRICT_MAX_VIDEO_AGE_DAYS,
            "relaxed": RELAXED_MAX_VIDEO_AGE_DAYS,
            "value": effective_max_video_age_days(
                policy, fallback=max_video_age_days(mode)
            ),
        },
        "min_intent_terms": {
            "strict": STRICT_MIN_INTENT_TERMS,
            "relaxed": RELAXED_MIN_INTENT_TERMS,
            "value": min_intent_terms(policy),
        },
        "relaxable_reasons": sorted(relaxable_reasons(policy)),
        "never_relaxed": list(NEVER_RELAXED),
    }


__all__ = [
    "BODY_HIDE_FAVORITES_KEY",
    "BODY_STRICT_KEY",
    "DEFAULT_MODE",
    "HIDE_FAVORITES_POLICY_KEY",
    "MODE_RELAXED",
    "MODE_STRICT",
    "NEVER_RELAXED",
    "POLICY_KEY",
    "RELAXABLE_QUALIFICATION_REASONS",
    "RELAXATION_SCHEMA",
    "RELAXED_MAX_VIDEO_AGE_DAYS",
    "RELAXED_MIN_INTENT_TERMS",
    "STALE_ACTIVITY_NOTE",
    "STRICT_MAX_VIDEO_AGE_DAYS",
    "STRICT_MIN_INTENT_TERMS",
    "TEAM_FAVORITE_NOTE",
    "activity_age_days",
    "annotate_if_team_favorite",
    "annotate_stale_activity",
    "annotate_stale_activity_all",
    "annotate_team_favorite",
    "apply_favorite_policy",
    "effective_max_video_age_days",
    "hide_team_favorites",
    "is_relaxed",
    "is_team_favorite",
    "max_video_age_days",
    "min_intent_terms",
    "normalize_mode",
    "policy_mode",
    "relaxable_reasons",
    "relaxation_receipt",
    "resolve_hide_team_favorites",
    "resolve_mode",
]
