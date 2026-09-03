"""payload 等价化(9.3):把 /kol-smart-search 的 body 补成与前端「全网查找」腿等价的 body。

前端 ``smartKolSearchProfileAdvanceJob`` 送的 body 是唯一真基准。少送任何一个键,
``profile_discovery_queue._smart_profile_payload`` 就换一套默认值兜底(advance_limit
30→15、每平台上限 20→45、代表作 1→None……),跑出来的不是一回事。

**两种病要分开治,不能都靠「假值才覆盖」:**

1. *键缺席* —— 补上默认值就好(setdefault 语义,操作员显式送来的值一律不动)。
2. *同名不同轴* —— 键**在**,值也非假,但它说的是召回那一次请求的事。这种必须先从合并体
   里摘掉、再按抓取轴的真值注入。``RECALL_ONLY_KEYS`` / ``CRAWL_AXIS_VALUES`` 就是这一刀:
   ``max_posts`` 在召回轴上是 URL 预览深度(前端对文本搜索也无条件写死 3),在抓取轴上是
   每个人真正抓几条(前端那条腿送 12)。3 不是假值,靠「假值才覆盖」永远治不了它。
"""
from __future__ import annotations

from typing import Any, Mapping

from app.domains.kol.discovery_filters import _text
from app.domains.kol.search_escalation_contract import (
    CANDIDATE_LIMIT,
    CRAWL_AXIS_VALUES,
    DEFAULT_STRATEGY,
    LOCAL_QUALIFICATION_SPEC,
    ONLINE_QUALIFICATION_SPEC,
    PER_PLATFORM_LIMITS,
    RECALL_ONLY_KEYS,
    REPRESENTATIVE_VIDEO_LIMIT,
    RESULT_LIMIT,
    STRATEGY_POLICY,
    _mapping,
)


def _strategy(body: Mapping[str, Any]) -> dict[str, Any]:
    key = _text(body.get("search_strategy")).lower()
    creator, reviewer, discovery, per_platform, core, expansion, exploration = (
        STRATEGY_POLICY.get(key) or STRATEGY_POLICY[DEFAULT_STRATEGY]
    )
    return {
        "creator_quota": creator,
        "reviewer_quota": reviewer,
        "new_discovery_limit": discovery,
        "per_platform_limit": per_platform,
        "bucket_policy": {"core_vertical": core, "expansion": expansion, "exploration": exploration},
    }


def _crawl_axis_defaults(body: Mapping[str, Any]) -> dict[str, Any]:
    """抓取轴上「缺席就补」的那批键。注释写的是缺席的代价,不是这个值本身。"""
    policy = _strategy(body)
    return {
        # 少了它 → _requests_smart_online_30 认不出严格 30 人合同 → 发现上限 50→15。
        "online_qualification_spec": dict(ONLINE_QUALIFICATION_SPEC),
        # 少了它 → _requests_smart_local_30 认不出 → advance_limit 封顶 30→15。
        "local_qualification_spec": dict(LOCAL_QUALIFICATION_SPEC),
        "new_discovery_limit": policy["new_discovery_limit"],
        "new_discovery_per_platform_limit": policy["per_platform_limit"],
        "new_discovery_per_platform_limits": dict(PER_PLATFORM_LIMITS),
        "advance_limit": RESULT_LIMIT,
        "representative_video_limit": REPRESENTATIVE_VIDEO_LIMIT,
        "candidate_limit": CANDIDATE_LIMIT,
        "limit": RESULT_LIMIT,
        "result_limit": RESULT_LIMIT,
        "creator_quota": policy["creator_quota"],
        "reviewer_quota": policy["reviewer_quota"],
        "bucket_policy": dict(policy["bucket_policy"]),
        "search_strategy": DEFAULT_STRATEGY,
    }


def escalation_advance_body(
    body: Mapping[str, Any], *, platforms: tuple[str, ...], query_text: str, session_id: int | None = None,
) -> dict[str, Any]:
    """把 /kol-smart-search 的 body 补成与前端「全网查找」腿逐字等价的 body。

    顺序是硬约束:**先摘召回轴的键,再补缺席的默认值,最后无条件写死抓取轴的真值。**
    最后一步不看合并体里有没有 —— 它治的正是「键在、值非假、但轴不对」那一类。
    """
    merged: dict[str, Any] = {
        key: value for key, value in _mapping(body).items() if key not in RECALL_ONLY_KEYS
    }
    # 平台是操作员的选择,这里是唯一真值,不许下游再兜底放宽。
    merged.update({
        "original_query_text": query_text,
        "include_new_discovery": True,
        "new_discovery_platforms": list(platforms),
    })
    for key, value in _crawl_axis_defaults(body).items():
        if merged.get(key) in (None, "", [], {}):
            merged[key] = value
    merged.update(CRAWL_AXIS_VALUES)
    if session_id:
        merged["session_id"] = int(session_id)
    return merged


__all__ = ["escalation_advance_body"]
