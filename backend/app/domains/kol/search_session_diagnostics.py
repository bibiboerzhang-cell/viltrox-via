"""搜索会话的可观测落库(车道 4·A7+A8)。

本模块只做两件事,且**全是加法**——不改任何过滤行为、不写 ``viltrox_fit_score``、不碰 rule_v0:

A7-a 「在线段结果落库」
    严格在线模式跳过 ``attach_new_discovery_result``,于是「平台给 60 → 本地闸砍到 47 →
    在线严格闸再砍到 12」这段坍缩在库里零痕迹,唯一证据是会滚掉的 INFO 日志。
    ``build_discovery_funnel`` 把每一层的进 / 出数量与丢弃原因分布拼成一份诊断,
    ``record_search_diagnostics`` 把它并进会话 ``result_summary``(纯 merge patch)。

A7-b 「profile-advance 第二段请求体落库」
    ``ensure_session_for_result`` 传 session_id 时直接 ``get_session`` 返回,不覆写
    ``input_payload_json`` —— 真正产出结果的那次请求没有原始留痕。这里用**单独的键**
    (``advance_request_snapshots``)追加第二段请求的 filter 快照,第一段的原始 input
    原样不动,两段各自的真相都在。

写入方向必须安全:诊断落库失败绝不能拖垮搜索,但也绝不静默——一律 ``logger.warning``
带 exc_info,调用方拿到 ``{"status": "failed", ...}`` 自行决定(现有调用方都选择继续)。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

ADVANCE_REQUEST_SNAPSHOTS_KEY = "advance_request_snapshots"
FILTER_SNAPSHOT_SCHEMA = "advance_request_filter_snapshot_v1"
DISCOVERY_FUNNEL_SCHEMA = "discovery_gate_funnel_v1"
DISCOVERY_FUNNEL_KEY = "discovery_funnel"
MAX_SNAPSHOTS = 6
_MAX_LIST_ITEMS = 12
_MAX_CODE_LEN = 40

# 快照只收「筛选面」——决定谁被留下的那些键。自由文本(query_text / persona / bio)一律
# 不收:留痕的目的是复盘筛选口径,不是复制请求体,更不是给联系方式开后门。
_SNAPSHOT_LIST_KEYS = (
    "platforms",
    "discovery_platforms",
    "new_discovery_platforms",
    "countries",
    "languages",
    "content_languages",
    "profile_types",
    "kol_types",
    "verticals",
)
_SNAPSHOT_CODE_KEYS = (
    "market",
    "country",
    "platform",
    "gear_content",
    "mode",
    "advance_mode",
    "search_strategy",
)
_SNAPSHOT_INT_KEYS = (
    "followers_min",
    "followers_max",
    "follower_min",
    "follower_max",
    "limit",
    "result_limit",
    "candidate_count",
    "candidate_limit",
    "advance_limit",
    "creator_quota",
    "reviewer_quota",
    "new_discovery_limit",
    "new_discovery_per_platform_limit",
)
_SNAPSHOT_BOOL_KEYS = (
    "exclude_chinese",
    "include_new_discovery",
    "include_discovery",
    "allow_backfill",
    "dedupe",
    "execute",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _code(value: Any) -> str:
    """把一个筛选值压成可公开的短码:只留字母数字与 ``_-.``,超长截断。"""

    raw = _text(value)[:_MAX_CODE_LEN]
    return "".join(char for char in raw if char.isalnum() or char in "_-.")


def _codes(value: Any) -> list[str]:
    source = value if isinstance(value, (list, tuple, set)) else ([value] if value else [])
    out: list[str] = []
    for entry in list(source)[:_MAX_LIST_ITEMS]:
        code = _code(entry)
        if code and code not in out:
            out.append(code)
    return sorted(out)


def _count(value: Any) -> int | None:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return None
    return max(0, min(parsed, 5_000_000_000))


def _filter_face(source: dict[str, Any]) -> dict[str, Any]:
    """从一层请求体里投影出筛选面。缺席就是缺席,绝不补默认值冒充操作员的选择。"""

    face: dict[str, Any] = {}
    for key in _SNAPSHOT_LIST_KEYS:
        if key in source:
            face[key] = _codes(source.get(key))
    for key in _SNAPSHOT_CODE_KEYS:
        if key in source:
            face[key] = _code(source.get(key))
    for key in _SNAPSHOT_INT_KEYS:
        if key in source:
            number = _count(source.get(key))
            if number is not None:
                face[key] = number
    for key in _SNAPSHOT_BOOL_KEYS:
        if key in source:
            face[key] = bool(source.get(key))
    return face


def project_filter_snapshot(
    body: Any,
    *,
    stage: str = "profile_advance",
    source: str = "",
) -> dict[str, Any]:
    """把一次请求体投影成 filter 快照。纯函数,零 IO。"""

    raw = _dict(body)
    nested = _filter_face(_dict(raw.get("filters")))
    top = _filter_face(raw)
    return {
        "schema": FILTER_SNAPSHOT_SCHEMA,
        "stage": _code(stage) or "profile_advance",
        "source": _code(source),
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # 两层各自留痕:``filters`` 是操作员显式勾的,顶层键是端点自己补进去的。
        "filters": nested,
        "body_filters": top,
        "filters_present": bool(nested or top),
    }


def _snapshot_fingerprint(snapshot: dict[str, Any]) -> str:
    face = {key: snapshot.get(key) for key in ("stage", "filters", "body_filters")}
    return repr(sorted(face.items(), key=lambda pair: pair[0]))


def project_advance_request_snapshots(value: Any) -> list[dict[str, Any]]:
    """读回 / 复写侧的形状闸:只认本模块写出的快照形状,其余一律丢弃。"""

    source = value if isinstance(value, list) else []
    out: list[dict[str, Any]] = []
    for entry in source[-MAX_SNAPSHOTS:]:
        raw = _dict(entry)
        if _text(raw.get("schema")) != FILTER_SNAPSHOT_SCHEMA:
            continue
        out.append({
            "schema": FILTER_SNAPSHOT_SCHEMA,
            "stage": _code(raw.get("stage")) or "profile_advance",
            "source": _code(raw.get("source")),
            "recorded_at": _text(raw.get("recorded_at"))[:32],
            "filters": _filter_face(_dict(raw.get("filters"))),
            "body_filters": _filter_face(_dict(raw.get("body_filters"))),
            "filters_present": raw.get("filters_present") is True,
        })
    return out


def record_advance_request_snapshot(
    session_id: int,
    *,
    body: Any,
    stage: str = "profile_advance",
    source: str = "",
    get_conn_fn: Any = None,
) -> dict[str, Any]:
    """把第二段请求的 filter 快照追加进 ``input_payload_json`` 的独立键。

    第一段的原始 input 键原样不动;快照列表封顶 ``MAX_SNAPSHOTS`` 条,与上一条完全同形
    则跳过写入(重放 / 轮询不制造噪声)。
    """

    snapshot = project_filter_snapshot(body, stage=stage, source=source)
    try:
        from app.db.connection import get_conn

        conn = (get_conn_fn or get_conn)()
        row = conn.execute(
            "SELECT input_payload_json FROM vkpi_kol_search_sessions WHERE id=?",
            (int(session_id),),
        ).fetchone()
        if not row:
            return {"status": "skipped", "reason": "session_not_found"}
        import json

        raw_payload = dict(row).get("input_payload_json")
        if isinstance(raw_payload, (str, bytes)):
            raw_payload = json.loads(raw_payload or "{}")
        payload = _dict(raw_payload)
        history = project_advance_request_snapshots(payload.get(ADVANCE_REQUEST_SNAPSHOTS_KEY))
        if history and _snapshot_fingerprint(history[-1]) == _snapshot_fingerprint(snapshot):
            return {"status": "skipped", "reason": "unchanged", "count": len(history)}
        history.append(snapshot)
        payload[ADVANCE_REQUEST_SNAPSHOTS_KEY] = history[-MAX_SNAPSHOTS:]
        conn.execute(
            "UPDATE vkpi_kol_search_sessions SET input_payload_json=?::jsonb WHERE id=?",
            (json.dumps(payload, ensure_ascii=False, default=str), int(session_id)),
        )
        conn.commit()
        return {"status": "recorded", "count": len(payload[ADVANCE_REQUEST_SNAPSHOTS_KEY])}
    except Exception as exc:  # 诊断落库失败绝不拖垮搜索,但必须留声
        logger.warning(
            "advance_request_snapshot_not_recorded session_id=%s reason=%s",
            session_id, str(exc)[:200], exc_info=True,
        )
        return {"status": "failed", "reason": "snapshot_write_failed"}


def provider_gate_funnel(
    *,
    platform_results: Any = None,
    gate_dropped: Any = None,
    survivors: int = 0,
    returned_new_creators: int = 0,
    existing_matched: int = 0,
) -> dict[str, Any]:
    """一次 provider 调用的漏斗切片:平台原始给量 → 逐闸丢弃 → 存活 → top-N 截断后返回。

    纯搬运既有计数,不重判任何候选。两类键**不算丢弃**,只留在明细里:
    ``brand_official_lexicon`` / ``brand_official_dynamic`` 是 ``brand_official`` 的
    子计数(重复计数),``*_penalized`` 是排序扣分而非丢弃。

    ``unattributed_dropped`` 是诚实的余项:平台给量减去(平台不符 + 库内已有 + 已分项闸 +
    存活)之后剩下的那些人 —— 目前对应尚未分项计数的去重 / 垃圾号 / 地区闸。宁可标成
    「未归因」也不硬塞进某一道闸冒充精确。
    """

    returned_by_platform: dict[str, int] = {}
    mismatch_total = 0
    platform_total = 0
    for entry in (platform_results if isinstance(platform_results, list) else []):
        row = _dict(entry)
        platform = _code(row.get("platform"))
        returned = _count(row.get("returned")) or 0
        if platform:
            returned_by_platform[platform] = returned_by_platform.get(platform, 0) + returned
        platform_total += returned
        mismatch_total += _count(row.get("filtered_platform_mismatch")) or 0
    dropped: dict[str, int] = {}
    for reason, count in _dict(gate_dropped).items():
        number = _count(count)
        code = _code(reason)
        if code and number is not None:
            dropped[code] = number
    dropped_total = sum(
        value for key, value in dropped.items()
        if not key.startswith("brand_official_") and not key.endswith("_penalized")
    )
    survivor_count = max(0, int(survivors))
    returned_count = max(0, int(returned_new_creators))
    existing_count = max(0, int(existing_matched))
    return {
        "platform_returned": dict(sorted(returned_by_platform.items())),
        "platform_returned_total": platform_total,
        "platform_mismatch_dropped": mismatch_total,
        "existing_matched": existing_count,
        "gate_dropped": dict(sorted(dropped.items())),
        "gate_dropped_total": dropped_total,
        "unattributed_dropped": max(
            0,
            platform_total - mismatch_total - existing_count - dropped_total - survivor_count,
        ),
        "survivors": survivor_count,
        "returned_new_creators": returned_count,
        "truncated_by_limit": max(0, survivor_count - returned_count),
    }


def build_discovery_funnel(
    *,
    lane: str,
    provider_funnels: list[dict[str, Any]] | None = None,
    online_contract: dict[str, Any] | None = None,
    discovery_counts: dict[str, Any] | None = None,
    returned_count: int | None = None,
) -> dict[str, Any]:
    """拼出「平台原始给量 → 本地闸 → 在线严格闸 → 落库」的完整漏斗。

    ``provider_funnels`` 是每一轮 provider 调用自带的 ``discovery_funnel``(严格在线模式
    可能多轮);在线段的严格闸计数从 ``online_contract`` 取。全部是既有事实的搬运,
    不重新判定任何候选。
    """

    rounds = [_dict(entry) for entry in (provider_funnels or []) if isinstance(entry, dict)]
    platform_returned: dict[str, int] = {}
    gate_dropped: dict[str, int] = {}
    totals = {
        "platform_returned_total": 0,
        "platform_mismatch_dropped": 0,
        "existing_matched": 0,
        "gate_dropped_total": 0,
        "unattributed_dropped": 0,
        "survivors": 0,
        "returned_new_creators": 0,
        "truncated_by_limit": 0,
    }
    for entry in rounds:
        for platform, count in _dict(entry.get("platform_returned")).items():
            number = _count(count)
            if number is not None:
                platform_returned[_code(platform)] = platform_returned.get(_code(platform), 0) + number
        for reason, count in _dict(entry.get("gate_dropped")).items():
            number = _count(count)
            if number is not None:
                gate_dropped[_code(reason)] = gate_dropped.get(_code(reason), 0) + number
        for key in totals:
            number = _count(entry.get(key))
            if number is not None:
                totals[key] += number

    funnel: dict[str, Any] = {
        "schema": DISCOVERY_FUNNEL_SCHEMA,
        "lane": _code(lane) or "unknown",
        "provider_rounds": len(rounds),
        "platform_returned": dict(sorted(platform_returned.items())),
        "gate_dropped": dict(sorted(gate_dropped.items())),
        **totals,
    }
    counts = _dict(discovery_counts)
    if counts:
        funnel["provider_counts"] = {
            key: _count(counts.get(key))
            for key in ("new_creators", "existing_matches", "auto_enrolled", "analyzing")
            if _count(counts.get(key)) is not None
        }
    contract = _dict(online_contract)
    if contract:
        strict: dict[str, Any] = {}
        for key in (
            "evaluated_count", "strict_qualified_count", "net_new_accepted_count",
            "returned_count", "pending_count", "rejected_count",
            "duplicate_local_inventory_count", "duplicate_online_count", "shortfall",
        ):
            number = _count(contract.get(key))
            if number is not None:
                strict[key] = number
        rejected: dict[str, int] = {}
        for reason, count in list(_dict(contract.get("rejected_by_reason")).items())[:48]:
            number = _count(count)
            if number is not None and _code(reason):
                rejected[_code(reason)] = number
        strict["rejected_by_reason"] = rejected
        funnel["online_strict"] = strict
    if returned_count is not None:
        funnel["session_returned_count"] = _count(returned_count) or 0
    return funnel


def record_search_diagnostics(session_id: int, patch: dict[str, Any]) -> dict[str, Any]:
    """把诊断并进会话 ``result_summary``(merge patch,保留会话既有状态)。

    必须在本次 ``attach_*`` 之后调用:``attach_recall_result`` / ``record_items`` 是整块
    覆写 ``result_summary_json`` 的,先写会被盖掉。
    """

    safe_patch = _dict(patch)
    if not safe_patch:
        return {"status": "skipped", "reason": "empty_patch"}
    try:
        from app.db.connection import get_conn
        from app.domains.kol import search_sessions

        # 只读一列 status:诊断不该为了拿状态去跑整套 get_session 投影(项/预览/展示闸)。
        row = get_conn().execute(
            "SELECT status FROM vkpi_kol_search_sessions WHERE id=?",
            (int(session_id),),
        ).fetchone()
        if not row:
            return {"status": "skipped", "reason": "session_not_found"}
        search_sessions.update_session_result_summary(
            int(session_id),
            status=_text(dict(row).get("status")) or "running",
            summary_patch=safe_patch,
        )
        return {"status": "recorded", "keys": sorted(safe_patch)}
    except Exception as exc:  # 同上:失败留声不静默,但绝不阻断管线
        logger.warning(
            "search_diagnostics_not_recorded session_id=%s reason=%s",
            session_id, str(exc)[:200], exc_info=True,
        )
        return {"status": "failed", "reason": "diagnostics_write_failed"}
