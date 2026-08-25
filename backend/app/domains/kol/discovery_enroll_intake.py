"""发现项 → 建档入库的「入口层」:载荷成形 + 写端闸的诚实原因记录。

从 ``profile_discovery_provider._auto_enroll_discoveries`` 抽出来的两件小事,和它自己的
编排职责分开(2026-08-25 合并波,provider 触到 800 行软棘轮时的正当拆分,不是刷快照):

- ``enroll_profile_payload`` —— 把一条发现项摊平成 profile-basics 载荷(字段口径与取值优先级
  逐字保持原样;此处只搬家,不改一个字段);
- 写端两道闸的**诚实原因标**。建档入口现在有两道闸,而且两道的失败形态不一样:

  1. ``discovery_filters.discovery_account_gate_verdict`` —— **抛** ``ValueError``
     (``discovery_account_rejected:<verdict>``)。以前它和「网络挂了」一样只进日志,
     调用方数不出「拦了几个」;
  2. ``brand_official_gate`` —— 返回 ``{"skipped": True, "skip_reason": ...}``。

  两种都在发现项上打同一个标(``auto_enroll_skipped``),调用方一把数清。
  认不出的异常照旧只记日志——不猜、不编。

纯函数模块:零库、零网络、零 LLM。红线:零触 ``viltrox_fit_score`` / rule_v0。
"""
from __future__ import annotations

from typing import Any

from app.core.coerce import _text
# followers 的取整口径刻意沿用 discovery_filters._int(与搬家前逐字同一个函数),
# 不换成 core.coerce._int0——那是另一份实现,换了就得重新论证「行为不变」。
from app.domains.kol.discovery_filters import _int

# 建档入口那道闸抛错时的原因前缀(单一真源,provider 与本模块都不许各写各的字面量)。
GATE_REJECTION_PREFIX = "discovery_account_rejected:"
# 打在发现项上的原因标字段名。
AUTO_ENROLL_SKIP_FIELD = "auto_enroll_skipped"


def enroll_profile_payload(item: dict[str, Any], platform: str, handle: str) -> dict[str, Any]:
    """把一条发现项摊平成 profile-basics 写入载荷(字段口径逐字沿用旧码)。"""
    profile_url = _text(item.get("profile_url") or item.get("channel_url") or item.get("url"))
    channel_id = _text(item.get("channel_id") or item.get("channelId"))
    account_id = _text(item.get("account_id") or item.get("accountId"))
    platform_user_id = _text(item.get("platform_user_id"))
    return {
        "platform": platform,
        "handle": handle,
        # 线上修(2026-07-10):入库行此前不带名字,列表/抽屉只剩 handle(YT=UC 频道 ID 串)。
        "display_name": _text(
            item.get("display_name") or item.get("name") or item.get("title") or item.get("channel_name")
        ),
        "profile_url": profile_url,
        "avatar_url": _text(item.get("avatar_url") or item.get("avatar")),
        "bio": _text(item.get("bio") or item.get("description") or item.get("snippet")),
        # 诚实回填(2026-07-12 两粉号案随手修):followers 只写真粉丝族;未知写 NULL,
        # 绝不再拿 avg_views 冒充粉丝数、也不把「未知」编成 0——否则第二道闸
        # (followers 已知才推荐)会被杜撰值穿透。真值由 buildout 深爬回填。
        "followers": _int(
            item.get("followers") or item.get("subscriber_count") or item.get("follower_count") or 0
        ) or None,
        # Although these are outside PROFILE_BASICS_WHITELIST, the writer
        # reads them before projection for canonical identity matching.
        "channel_id": channel_id,
        "account_id": account_id,
        "platform_user_id": platform_user_id,
        # 同一身份真源:把 provider 同时给出的 UC channel id / @handle 留在原始 profile
        # 身份包。后续 URL 深爬或导入即使只带其中一条别名,也能命中本行,
        # 不再依赖 (platform,handle) 的单键偶然一致。
        "raw_platform_data": {
            "discovery_identity_v1": {
                "platform": platform,
                "handle": handle,
                "channel_id": channel_id,
                "account_id": account_id,
                "platform_user_id": platform_user_id,
                "profile_url": profile_url,
            }
        },
    }


def mark_writer_skip(item: dict[str, Any], enroll_result: Any) -> bool:
    """写端返回 skip(品牌官号建档闸)时留原因标。返回 True = 本条不算入库。"""
    if not isinstance(enroll_result, dict) or not enroll_result.get("skipped"):
        return False
    item[AUTO_ENROLL_SKIP_FIELD] = _text(enroll_result.get("skip_reason")) or "gated"
    return True


def mark_gate_rejection(item: dict[str, Any], exc: BaseException) -> bool:
    """建档入口那道闸抛错拦人时留原因标。认不出的异常返回 False(交给调用方照旧记日志)。"""
    text = _text(exc)
    if not isinstance(exc, ValueError) or GATE_REJECTION_PREFIX not in text:
        return False
    reason = text.split(GATE_REJECTION_PREFIX, 1)[-1].strip()
    if not reason:
        return False
    item[AUTO_ENROLL_SKIP_FIELD] = reason
    return True


def enroll_skip_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    """逐原因清点被写端拦下的发现项(0 档不补、也不汇总掉任何一档)。"""
    counts: dict[str, int] = {}
    for item in items or []:
        reason = _text((item or {}).get(AUTO_ENROLL_SKIP_FIELD))
        if reason:
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


__all__ = [
    "AUTO_ENROLL_SKIP_FIELD",
    "GATE_REJECTION_PREFIX",
    "enroll_profile_payload",
    "enroll_skip_counts",
    "mark_gate_rejection",
    "mark_writer_skip",
]
