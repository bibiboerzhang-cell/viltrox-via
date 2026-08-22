"""会话读端触达展示闸(第二道闸落点①,从 search_sessions 拆出以守千行线)。

会话项是搜索时的快照,档案补全回填 followers 后快照不会自己变——读端按 pool 现值实时重判。
判据复用 discovery_filters 单一真源;只挡/标注展示,绝不改写会话项/池行。
"""
from __future__ import annotations

import re
from typing import Any

from app.core.logging import get_logger
from app.domains.kol.discovery_filters import (
    LOW_REACH_FLAG_LIKE_PATTERN,
    _reach_display_state,
    _reach_floor_enabled,
    _reach_floor_min_followers,
)
from app.domains.kol.search_sessions_schema import REACH_GATED_ITEM_TYPES as _REACH_GATED_ITEM_TYPES
from app.domains.kol.search_sessions_serde import _int_or_none

logger = get_logger(__name__)


def _reach_gate_pool_rows(
    conn: Any,
    ids: list[int],
    pairs: list[tuple[str, str]],
) -> tuple[dict[int, dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    """批量取会话项对应的 pool 行现值(id 直查 + new_creator 按 platform/handle 反查)。

    new_creator 会话项 kol_pool_id 恒 NULL(设计不变量,见 approve_session 注释),但发现已
    自动入库 → 按 (platform, lower(handle)) 反查现值。返回 (by_id, by_pair);查询失败抛给调用方。
    """
    by_id: dict[int, dict[str, Any]] = {}
    by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    select_cols = (
        "SELECT id, platform, handle, followers, avg_views, avg_comments, engagement_rate, "
        "(raw_platform_data LIKE ?) AS low_reach_flagged FROM vkpi_kol_pool"
    )
    if ids:
        placeholders = ",".join(["?"] * len(ids))
        rows = conn.execute(
            f"{select_cols} WHERE id IN ({placeholders})",
            (LOW_REACH_FLAG_LIKE_PATTERN, *ids),
        ).fetchall()
        for row in rows:
            data = dict(row)
            by_id[int(data["id"])] = data
    if pairs:
        clauses = " OR ".join(["(lower(platform)=? AND lower(handle)=?)"] * len(pairs))
        params: list[Any] = [LOW_REACH_FLAG_LIKE_PATTERN]
        for platform, handle in pairs:
            params.extend([platform, handle])
        rows = conn.execute(f"{select_cols} WHERE {clauses}", tuple(params)).fetchall()
        for row in rows:
            data = dict(row)
            key = (str(data.get("platform") or "").lower(), str(data.get("handle") or "").lower())
            by_pair[key] = data
    return by_id, by_pair


_YT_CHANNEL_ID_IN_URL = re.compile(r"/channel/(UC[0-9A-Za-z_-]{10,})")


def _item_reach_pairs(item: dict[str, Any]) -> list[tuple[str, str]]:
    """会话项 → pool 行反查键列表(platform, handle 小写),按优先序。

    YouTube 池行以 UC 频道 id 当 handle(profile_basics 口径),而发现项 handle 是 @customUrl
    (会话 1106 案:gcrustypork ↔ 池行 UCjYD2…)→ 单键永远查不到、回落快照误判。补 channel_id
    /channel_url 里的 UC id 作第二键;其余平台保持单键语义不变。
    """
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    platform = str(payload.get("platform") or "").strip().lower()
    if not platform:
        return []
    pairs: list[tuple[str, str]] = []
    handle = str(payload.get("handle") or "").strip().lstrip("@").lower()
    if handle:
        pairs.append((platform, handle))
    if platform == "youtube":
        channel_id = str(payload.get("channel_id") or "").strip()
        if not channel_id:
            for key in ("channel_url", "source_url", "profile_url"):
                match = _YT_CHANNEL_ID_IN_URL.search(str(payload.get(key) or ""))
                if match:
                    channel_id = match.group(1)
                    break
        if channel_id and (platform, channel_id.lower()) not in pairs:
            pairs.append((platform, channel_id.lower()))
    return pairs


def _item_reach_pair(item: dict[str, Any]) -> tuple[str, str] | None:
    pairs = _item_reach_pairs(item)
    return pairs[0] if pairs else None


def _apply_reach_display_gate(
    conn: Any,
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """会话读端触达展示闸:按 pool 现值三态过滤推荐/发现面会话项(第二道闸落点①)。

    - low_reach:followers 已知 < 门槛/互动实测全零/补全后 low_reach 标 → 不展示(计数折叠);
    - unknown:followers 未知 → 发现面(new_creator/existing_kol)照常上墙,项上标
      reach_status=analyzing、粉丝位由前端显示「粉丝数待核」(2026-08-22 裁决:不藏、不假排队),
      计入 visible_analyzing;推荐面 recall_candidate 仍折叠为「分析中 ×N」(hidden_analyzing);
    - ok:展示。pool 行缺时退回会话项 payload 实时判据;pool 现值已知而快照缺 followers 时把
      现值补进 payload(读端投影,不写库)。
    fail-open:池查询异常 → 原样返回全部项(过滤器绝不当故障放大器),计数带 error 标。
    红线:零写库;落库≠推荐——池行/会话项都保留,只挡本展示出口。
    """
    counts: dict[str, Any] = {
        "enabled": _reach_floor_enabled(),
        "min_followers": _reach_floor_min_followers(),
        "hidden_low_reach": 0,
        "hidden_analyzing": 0,
        "visible_analyzing": 0,
        "by_type": {},
    }
    if not items or not _reach_floor_enabled():
        return items, counts
    gated_idx = {
        i for i, item in enumerate(items)
        if str(item.get("item_type") or "") in _REACH_GATED_ITEM_TYPES
    }
    if not gated_idx:
        return items, counts
    ids = sorted({
        int(items[i]["kol_pool_id"]) for i in gated_idx
        if _int_or_none(items[i].get("kol_pool_id"))
    })
    pairs = sorted({
        pair for i in gated_idx
        if not _int_or_none(items[i].get("kol_pool_id"))
        for pair in _item_reach_pairs(items[i])
    })
    try:
        by_id, by_pair = _reach_gate_pool_rows(conn, ids, pairs)
    except Exception:
        logger.warning("reach display gate skipped(fail-open 不误杀)", exc_info=True)
        counts["error"] = "pool_lookup_failed"
        return items, counts

    visible: list[dict[str, Any]] = []
    for i, item in enumerate(items):
        if i not in gated_idx:
            visible.append(item)
            continue
        pool_row: dict[str, Any] | None = None
        pool_id = _int_or_none(item.get("kol_pool_id"))
        if pool_id:
            pool_row = by_id.get(int(pool_id))
        else:
            for pair in _item_reach_pairs(item):
                pool_row = by_pair.get(pair)
                if pool_row is not None:
                    break
        # pool 现值优先(补全回填后的真值);池行缺 → 退回会话项 payload 快照实时判据。
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        candidate = pool_row if pool_row is not None else payload
        state = _reach_display_state(candidate)
        type_key = str(item.get("item_type") or "unknown")
        type_counts = counts["by_type"].setdefault(
            type_key, {"hidden_low_reach": 0, "hidden_analyzing": 0, "visible_analyzing": 0}
        )
        if state == "ok":
            _project_reach_onto_payload(payload, pool_row, "ok")
            visible.append(item)
            continue
        if state == "unknown" and type_key in _DISCOVERY_FACE_ITEM_TYPES:
            _project_reach_onto_payload(payload, pool_row, "analyzing")
            counts["visible_analyzing"] += 1
            type_counts["visible_analyzing"] += 1
            visible.append(item)
            continue
        bucket = "hidden_low_reach" if state == "low_reach" else "hidden_analyzing"
        counts[bucket] += 1
        type_counts[bucket] += 1
    return visible, counts


# 发现面(框3「全网新发现/库内已有」):followers 未知照常上墙标「粉丝数待核」;推荐面 recall_candidate 不在此列。
_DISCOVERY_FACE_ITEM_TYPES = frozenset({"new_creator", "existing_kol"})


def _project_reach_onto_payload(payload: dict[str, Any], pool_row: dict[str, Any] | None, state: str) -> None:
    """读端投影(不写库):reach_status 以实时判据为准;快照缺 followers 而 pool 现值已知 → 补进。"""
    if not isinstance(payload, dict):
        return
    payload["reach_status"] = state
    if payload.get("followers") in (None, "", 0) and isinstance(pool_row, dict):
        followers = _int_or_none(pool_row.get("followers"))
        if followers:
            payload["followers"] = followers


__all__ = [
    "_apply_reach_display_gate",
    "_item_reach_pair",
    "_item_reach_pairs",
    "_project_reach_onto_payload",
    "_reach_gate_pool_rows",
]
