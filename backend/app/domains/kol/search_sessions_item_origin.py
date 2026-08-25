"""会话项来源(origin)的唯一真源:一处推断,写端/回填/汇总共用。

背景(2026-08-25 用户诉求):搜索结果里必须一眼看出「哪些人是从自有库里捞的、
哪些是本次现场从平台上新找到的」。此前 ``vkpi_kol_search_session_items``
没有来源列,前端只对 ``new_creator`` / ``existing_kol`` 两类贴标签,占比最大的
``recall_candidate``(prod 1401 条)一条都没有标记,只能靠读端猜。

线上实测(2026-08-25 只读探针,3939 行)证实一个必须写进规则里的陷阱:

    item_type                     origin_lane   payload.source                行数
    recall_candidate              -             -                             1401
    new_creator                   -             platform_discovery            1100
    url_profile                   -             -                              952
    existing_kol                  -             platform_discovery             427
    url_video                     -             -                               46
    online_qualified_candidate    online        platform_discovery_strict        9
    unknown                       -             -                                4

``existing_kol`` 也带 ``source=platform_discovery``(427/427)——它描述的是
「这一轮是在平台上又碰到了这个人」,而不是「这个人是新的」。所以 payload 标记
永远不能压过 ``item_type``:先按 item_type 判,payload 标记只用于旁证与兜底。

判不出来时返回 ``unknown``,绝不猜。
"""
from __future__ import annotations

from typing import Any

from app.domains.kol.search_sessions_serde import _dict, _text


# 落库的来源取值(与迁移 301 的 CHECK 约束字面同步)。
ITEM_ORIGIN_LOCAL_POOL = "local_pool"
ITEM_ORIGIN_ONLINE_NEW = "online_new"
ITEM_ORIGIN_OPERATOR_URL = "operator_url"
ITEM_ORIGIN_UNKNOWN = "unknown"

ITEM_ORIGIN_VALUES: tuple[str, ...] = (
    ITEM_ORIGIN_LOCAL_POOL,
    ITEM_ORIGIN_ONLINE_NEW,
    ITEM_ORIGIN_OPERATOR_URL,
    ITEM_ORIGIN_UNKNOWN,
)

# 尚未标注:迁移 301 之前写入的历史行(origin IS NULL)。它与 ``unknown`` 是两件事——
# ``unknown`` 是「看过证据仍判不出」,``unlabeled`` 是「这行还没被判过」。汇总里分开
# 报,不许合并,否则回填有没有跑过就看不出来了。
ITEM_ORIGIN_UNLABELED = "unlabeled"

ITEM_ORIGIN_LABELS: dict[str, str] = {
    ITEM_ORIGIN_LOCAL_POOL: "库内已有",
    ITEM_ORIGIN_ONLINE_NEW: "本次新发现",
    ITEM_ORIGIN_OPERATOR_URL: "手动录入",
    ITEM_ORIGIN_UNKNOWN: "来源未知",
    ITEM_ORIGIN_UNLABELED: "尚未标注",
}

ITEM_ORIGIN_SCHEMA = "session_item_origin_v1"

# item_type 是唯一被 CHECK 约束的写端字面量,也是最可靠的证据,故排在 payload 标记之前。
_ITEM_TYPE_ORIGIN: dict[str, str] = {
    "recall_candidate": ITEM_ORIGIN_LOCAL_POOL,
    "existing_kol": ITEM_ORIGIN_LOCAL_POOL,
    "online_qualified_candidate": ITEM_ORIGIN_ONLINE_NEW,
    "new_creator": ITEM_ORIGIN_ONLINE_NEW,
    "url_profile": ITEM_ORIGIN_OPERATOR_URL,
    "url_video": ITEM_ORIGIN_OPERATOR_URL,
}

# 现场发现侧的 payload 旁证:search_sessions_online.py 写 origin_lane="online",
# profile_online_qualification.py 写 source="platform_discovery_strict",
# search_sessions_attach.py 的发现墙写 source="platform_discovery"。
_ONLINE_LANE_MARKER = "online"
_ONLINE_SOURCE_PREFIX = "platform_discovery"

# 操作员贴链接侧的 payload 旁证:``url_type`` 只由 search_sessions_attach_jobs.py:38
# 那一条路径写(整表实测 url_profile 951 / url_video 43 / unknown 4,其余 item_type
# 一条都没有),所以它出现即代表「这是操作员自己贴进来的链接」——是结构性事实,不是猜。
# 它专治那 4 条 item_type='unknown':那不是「来路不明的人」,而是「贴了个我们认不出
# 平台的链接」。少了这一条,写端会判 unknown 而读端按 url_type 判「你提供的」,两边打架。
_OPERATOR_URL_MARKER_FIELD = "url_type"

_ORIGIN_PAYLOAD_FIELD = "origin"
_ORIGIN_REASON_PAYLOAD_FIELD = "origin_reason"


def _origin_markers(payload: Any) -> dict[str, str]:
    data = _dict(payload)
    return {
        "origin_lane": _text(data.get("origin_lane")).lower(),
        "source": _text(data.get("source")).lower(),
        _OPERATOR_URL_MARKER_FIELD: _text(data.get(_OPERATOR_URL_MARKER_FIELD)).lower(),
    }


def _has_online_marker(markers: dict[str, str]) -> bool:
    return (
        markers.get("origin_lane") == _ONLINE_LANE_MARKER
        or markers.get("source", "").startswith(_ONLINE_SOURCE_PREFIX)
    )


def explain_item_origin(item_type: Any, payload: Any = None) -> dict[str, Any]:
    """推断一条会话项的来源,并连同判据一起返回(可审计、可单测)。

    返回 ``{"origin", "reason", "item_type", "markers"}``。``reason`` 记录到底是哪条
    规则命中的,回填脚本与排查都读它,不必反推。
    """
    normalized_type = _text(item_type).lower()
    markers = _origin_markers(payload)
    mapped = _ITEM_TYPE_ORIGIN.get(normalized_type)
    if mapped:
        reason = f"item_type:{normalized_type}"
        if mapped == ITEM_ORIGIN_ONLINE_NEW and _has_online_marker(markers):
            # 旁证与 item_type 一致时记下来,便于日后收紧规则时区分「双证」与「单证」。
            reason = f"item_type:{normalized_type}+payload_marker"
        return {
            "origin": mapped,
            "reason": reason,
            "item_type": normalized_type,
            "markers": markers,
        }
    # item_type 不在已知集合内(线上有 4 条 item_type='unknown')。此时只认现场发现的
    # 显式标记;没有标记就诚实报 unknown,不拿 kol_pool_id 之类的间接信号硬猜。
    if markers.get("origin_lane") == _ONLINE_LANE_MARKER:
        return {
            "origin": ITEM_ORIGIN_ONLINE_NEW,
            "reason": "payload_origin_lane",
            "item_type": normalized_type,
            "markers": markers,
        }
    if markers.get("source", "").startswith(_ONLINE_SOURCE_PREFIX):
        return {
            "origin": ITEM_ORIGIN_ONLINE_NEW,
            "reason": "payload_source",
            "item_type": normalized_type,
            "markers": markers,
        }
    if markers.get(_OPERATOR_URL_MARKER_FIELD):
        return {
            "origin": ITEM_ORIGIN_OPERATOR_URL,
            "reason": "payload_url_type",
            "item_type": normalized_type,
            "markers": markers,
        }
    return {
        "origin": ITEM_ORIGIN_UNKNOWN,
        "reason": "no_origin_evidence",
        "item_type": normalized_type,
        "markers": markers,
    }


def infer_item_origin(item_type: Any, payload: Any = None) -> str:
    """``explain_item_origin`` 的取值快捷方式,永远返回 ``ITEM_ORIGIN_VALUES`` 之一。"""
    return explain_item_origin(item_type, payload)["origin"]


def origin_label(origin: Any) -> str:
    """来源的中文门面文案;未知取值回落到「来源未知」而不是原样透出内部字面量。"""
    return ITEM_ORIGIN_LABELS.get(_text(origin).lower(), ITEM_ORIGIN_LABELS[ITEM_ORIGIN_UNKNOWN])


def apply_item_origin_to_payload(item_type: Any, payload: Any) -> dict[str, Any]:
    """把来源写进 payload 自描述字段,返回新 dict(不改调用方传入的对象)。

    列(``origin``)支撑按来源统计与筛选,payload 里的同名字段让每条记录自带口径——
    历史会话、导出快照、以及尚未把新列接进投影的读路径都能直接读到。
    """
    data = dict(_dict(payload))
    verdict = explain_item_origin(item_type, data)
    data[_ORIGIN_PAYLOAD_FIELD] = verdict["origin"]
    data[_ORIGIN_REASON_PAYLOAD_FIELD] = verdict["reason"]
    return data


def payload_origin(payload: Any) -> str:
    """读取 payload 里已落库的来源;没有则返回空串(交由调用方决定是否重算)。"""
    value = _text(_dict(payload).get(_ORIGIN_PAYLOAD_FIELD)).lower()
    return value if value in ITEM_ORIGIN_VALUES else ""


def empty_origin_counts() -> dict[str, int]:
    counts = {value: 0 for value in ITEM_ORIGIN_VALUES}
    counts[ITEM_ORIGIN_UNLABELED] = 0
    return counts


def origin_breakdown_from_pairs(pairs: Any) -> dict[str, Any]:
    """把 ``(origin, item_type, count)`` 三元组聚成会话汇总要用的来源分布。

    纯函数:``session_origin_breakdown``(读库)与回填脚本(读文件/批次)共用同一套
    口径,保证「会话诊断里看到的数」和「回填打印的数」永远是一个算法算出来的。
    """
    counts = empty_origin_counts()
    by_item_type: dict[str, dict[str, int]] = {}
    total = 0
    for pair in pairs or []:
        origin_raw, item_type_raw, count_raw = pair
        try:
            count = int(count_raw)
        except (TypeError, ValueError):
            count = 0
        if count <= 0:
            continue
        origin = _text(origin_raw).lower()
        if origin not in counts:
            # NULL / 空 / 未知字面量都归入「尚未标注」,而不是伪装成 unknown。
            origin = ITEM_ORIGIN_UNLABELED if not origin else ITEM_ORIGIN_UNKNOWN
        item_type = _text(item_type_raw).lower() or "unknown"
        counts[origin] = counts.get(origin, 0) + count
        bucket = by_item_type.setdefault(item_type, {})
        bucket[origin] = bucket.get(origin, 0) + count
        total += count
    return {
        "schema": ITEM_ORIGIN_SCHEMA,
        "total": total,
        "counts": counts,
        "labels": dict(ITEM_ORIGIN_LABELS),
        "by_item_type": by_item_type,
    }


def session_origin_breakdown(conn: Any, session_id: int) -> dict[str, Any]:
    """按会话直接从库里聚合来源分布——权威口径,前端不用自己数。

    一条 GROUP BY 走 ``idx_vkpi_kol_search_session_items_session_origin``(迁移 301)。
    """
    rows = conn.execute(
        """
        SELECT origin AS origin,
               item_type AS item_type,
               COUNT(*) AS item_count
        FROM vkpi_kol_search_session_items
        WHERE session_id=?
        GROUP BY origin, item_type
        """,
        (int(session_id),),
    ).fetchall()
    pairs = []
    for raw in rows or []:
        row = dict(raw)
        pairs.append((row.get("origin"), row.get("item_type"), row.get("item_count")))
    return origin_breakdown_from_pairs(pairs)
