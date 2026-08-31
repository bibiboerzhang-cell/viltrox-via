"""domains/kol/discovery_term_yield.py — 逐词台账的跨会话只读聚合器(演进评审 3 号动作)。

写端在 ``profile_discovery_evidence``(键 ``result_summary_json->'discovery_term_evidence'``,
落 ``vkpi_kol_search_sessions``);本模块是它的第一个读端:把散在各会话里的逐词证据
收成「这条词在这段时间里烧了多少配额、换回几个合格新人」的台账。

口径三条,刻意写死:

* **v1/v2 归一 shim**。库存旧行是 v1(单桶 ``quota_units``、无 ``youtube_search_calls``),
  HEAD 写端是 v2(拆 ``youtube_search_calls`` / combined units)。归一按**行内键存在性**判:
  行里有 ``youtube_search_calls`` 就用它;没有(v1 行)则 YouTube 腿回落 ``search_calls``
  (v1 里每次 occurrence 即一次 search.list),非 YouTube 腿诚实记 0。裸按 v2 键聚合
  会在全部存量行取空——这正是本 shim 存在的理由。``quota_units`` 两版语义同源
  (每次 search.list 记 100 的旧记账,v2 仅标 deprecated),直接相加。
* **配额为 0 → 产出率是 None,不是 0**。「没烧配额」(Apify 腿 / 词被 skip)和
  「烧了配额零产出」是两个结论,绝不除零凑数、绝不混成一个 0。
* **样本荒必须可见**。每行带 ``sessions_count`` 与 ``first_seen``/``last_seen``,
  总账带 ``low_sample``:本地现在只有 2 个会话,消费端要能看出「数据不够别当真」。

零 LLM、零外调、零写库;不触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger(__name__)

TERM_YIELD_SCHEMA = "discovery_term_yield_v1"
TERM_EVIDENCE_KEY = "discovery_term_evidence"

DEFAULT_WINDOW_DAYS = 30
MAX_WINDOW_DAYS = 365

#: 会话数低于此值时置 ``low_sample=True`` —— 阈值只做展示提醒,不过滤任何行。
LOW_SAMPLE_SESSIONS = 5

#: 无逐词台账的行(旧 provider / IG·TT actor 腿)没有 anchor_source;不杜撰词梯档位。
UNLABELED_ANCHOR_SOURCE = "unlabeled"

_MAX_TERM_ROWS = 200

# 兼容层规矩:? 占位;jsonb 存在性判断必须用 jsonb_exists(...),不许 ? 算子
# (compat 层会把 ? 当占位符);聚合/表达式列一律带 AS。
_SESSIONS_SQL = """
SELECT id AS session_id,
       created_at AS created_at,
       result_summary_json -> 'discovery_term_evidence' AS term_evidence
  FROM vkpi_kol_search_sessions
 WHERE jsonb_exists(result_summary_json, 'discovery_term_evidence')
   AND created_at >= NOW() - make_interval(days => ?)
 ORDER BY created_at ASC, id ASC
"""


# ── 小工具 ─────────────────────────────────────────────────────────────────────
def _text(value: Any) -> str:
    return str(value or "").strip()


def _code(value: Any) -> str:
    return _text(value).lower()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _loads(value: Any) -> Any:
    """JSON 列容错读:compat 层可能回 dict,也可能回 str/bytes。解不开 → None,不杜撰。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return None


def _iso(value: Any) -> str:
    """created_at 归一成可比对的 ISO 文本(datetime → isoformat,其余按文本收)。"""
    iso = getattr(value, "isoformat", None)
    return iso() if callable(iso) else _text(value)


def _per_100_units(qualified: int, quota_units: int) -> float | None:
    """每 100 配额单位换回几个合格新人。配额为 0 → None:「没花钱」不是「零产出」。"""
    if quota_units <= 0:
        return None
    return round(qualified * 100.0 / quota_units, 2)


# ── v1/v2 归一 shim ────────────────────────────────────────────────────────────
def normalize_term_row(row: Any) -> dict[str, Any] | None:
    """把一条落库 term 行(v1 或 v2)归一成聚合器的统一形状。认不出 term → None。

    判版依据是**行内键存在性**而非顶层 schema 标:同一份证据里 booked / 未 booked
    的行键形可以不同,按行判才不会把混合行读劈。``normalized_from_v1`` 标出
    ``youtube_search_calls`` 是推导值(v1 行 YouTube 腿每 occurrence = 1 次 search.list),
    让消费端能看见 shim 动过哪里。
    """
    if not isinstance(row, dict):
        return None
    term = " ".join(_text(row.get("term")).split())
    platform = _code(row.get("platform"))
    if not term or not platform:
        return None
    search_calls = max(0, _int(row.get("search_calls")))
    raw_ytsc = row.get("youtube_search_calls")
    if raw_ytsc is not None:  # v2 行:写端已拆桶,原样收下。
        youtube_search_calls = max(0, _int(raw_ytsc))
        from_v1 = False
    else:  # v1 行:单桶时代,YouTube 腿每次 occurrence 就是一次 search.list。
        youtube_search_calls = search_calls if platform == "youtube" else 0
        from_v1 = True
    candidates = row.get("candidates_returned")
    anchored = bool(row.get("anchored"))
    return {
        "term": term,
        "platform": platform,
        "anchored": anchored,
        "anchor": _text(row.get("anchor")),
        # 无台账的行没有词梯档位;诚实标 unlabeled,不按 anchored 杜撰一个档位名。
        "anchor_source": _code(row.get("anchor_source")) or UNLABELED_ANCHOR_SOURCE,
        "search_calls": search_calls,
        "youtube_search_calls": youtube_search_calls,
        "quota_units": max(0, _int(row.get("quota_units"))),
        "qualified_new": max(0, _int(row.get("qualified_new"))),
        # shared_round 腿的写端就是 None(无法逐词归因),读端保留这个「不知道」。
        "candidates_returned": None if candidates is None else max(0, _int(candidates)),
        "exhausted": bool(row.get("exhausted")),
        "skipped": bool(_code(row.get("skipped"))),
        "normalized_from_v1": from_v1,
    }


def _evidence_schema_version(evidence: dict[str, Any]) -> str:
    label = _code(evidence.get("schema"))
    if label.endswith("_v1"):
        return "v1"
    if label.endswith("_v2"):
        return "v2"
    return "unknown"


# ── 聚合 ───────────────────────────────────────────────────────────────────────
def _new_term_acc(norm: dict[str, Any]) -> dict[str, Any]:
    return {
        "platform": norm["platform"],
        "anchor_source": norm["anchor_source"],
        "term": norm["term"],
        "anchor": "",
        "anchored": False,
        "sessions_count": 0,
        "first_seen": "",
        "last_seen": "",
        "search_calls": 0,
        "youtube_search_calls": 0,
        "quota_units": 0,
        "qualified_new": 0,
        "candidates_returned": 0,
        "candidates_unknown_sessions": 0,
        "exhausted_sessions": 0,
        "skipped_sessions": 0,
        "normalized_v1_rows": 0,
    }


def _fold_term(acc: dict[str, Any], norm: dict[str, Any], seen_at: str) -> None:
    acc["sessions_count"] += 1
    acc["anchored"] = acc["anchored"] or norm["anchored"]
    if norm["anchor"]:
        acc["anchor"] = norm["anchor"]
    if seen_at:
        acc["first_seen"] = min(acc["first_seen"], seen_at) if acc["first_seen"] else seen_at
        acc["last_seen"] = max(acc["last_seen"], seen_at) if acc["last_seen"] else seen_at
    acc["search_calls"] += norm["search_calls"]
    acc["youtube_search_calls"] += norm["youtube_search_calls"]
    acc["quota_units"] += norm["quota_units"]
    acc["qualified_new"] += norm["qualified_new"]
    if norm["candidates_returned"] is None:
        acc["candidates_unknown_sessions"] += 1
    else:
        acc["candidates_returned"] += norm["candidates_returned"]
    if norm["exhausted"]:
        acc["exhausted_sessions"] += 1
    if norm["skipped"]:
        acc["skipped_sessions"] += 1
    if norm["normalized_from_v1"]:
        acc["normalized_v1_rows"] += 1


def _fold_anchor_source(
    buckets: dict[str, dict[str, Any]], norm: dict[str, Any], seen_at: str
) -> None:
    bucket = buckets.setdefault(norm["anchor_source"], {
        "terms": set(),
        "session_rows": 0,
        "first_seen": "",
        "last_seen": "",
        "search_calls": 0,
        "youtube_search_calls": 0,
        "quota_units": 0,
        "qualified_new": 0,
        "exhausted_sessions": 0,
    })
    bucket["terms"].add((norm["platform"], norm["term"]))
    bucket["session_rows"] += 1
    if seen_at:
        bucket["first_seen"] = min(bucket["first_seen"], seen_at) if bucket["first_seen"] else seen_at
        bucket["last_seen"] = max(bucket["last_seen"], seen_at) if bucket["last_seen"] else seen_at
    bucket["search_calls"] += norm["search_calls"]
    bucket["youtube_search_calls"] += norm["youtube_search_calls"]
    bucket["quota_units"] += norm["quota_units"]
    bucket["qualified_new"] += norm["qualified_new"]
    if norm["exhausted"]:
        bucket["exhausted_sessions"] += 1


def aggregate_term_yield(conn: Any = None, *, days: int = DEFAULT_WINDOW_DAYS) -> dict[str, Any]:
    """跨会话聚合逐词证据:按 (platform, anchor_source, term) 出逐词账,另出词梯档位汇总。

    纯 SELECT、零写库、零 LLM。``conn`` 缺省用 ``get_conn()``(不当上下文管理器,
    连接归池子管);测试可注入假 conn。失败方向:读不出来 → ``status='probe_failed'``,
    绝不返回一个看起来像「零产出」的空账。
    """
    window = max(1, min(_int(days, DEFAULT_WINDOW_DAYS), MAX_WINDOW_DAYS))
    base = {
        "schema": TERM_YIELD_SCHEMA,
        "window_days": window,
        "note": (
            "逐词供给侧台账的只读聚合;v1 行的 youtube_search_calls 是 shim 推导值。"
            "qualified_per_100_units 为 None = 该词没烧过配额,不是零产出。"
            "low_sample=True 时样本不足,别当结论用。"
        ),
    }
    try:
        rows = (conn if conn is not None else get_conn()).execute(
            _SESSIONS_SQL, (window,)
        ).fetchall()
    except Exception as exc:
        logger.warning(
            "term_yield_probe_failed window_days=%s reason=%s",
            window, str(exc)[:200], exc_info=True,
        )
        return {**base, "status": "probe_failed", "reason": "term_yield_probe_failed"}

    terms: dict[tuple[str, str, str], dict[str, Any]] = {}
    buckets: dict[str, dict[str, Any]] = {}
    schema_versions = {"v1": 0, "v2": 0, "unknown": 0}
    sessions_scanned = 0
    sessions_used = 0
    sessions_unparseable = 0
    normalized_v1_rows = 0
    for raw in rows:
        row = dict(raw)
        sessions_scanned += 1
        evidence = _loads(row.get("term_evidence"))
        term_rows = evidence.get("terms") if isinstance(evidence, dict) else None
        if not isinstance(term_rows, list):
            sessions_unparseable += 1
            continue
        sessions_used += 1
        schema_versions[_evidence_schema_version(evidence)] += 1
        seen_at = _iso(row.get("created_at"))
        for term_row in term_rows:
            norm = normalize_term_row(term_row)
            if norm is None:
                continue
            if norm["normalized_from_v1"]:
                normalized_v1_rows += 1
            key = (norm["platform"], norm["anchor_source"], norm["term"])
            _fold_term(terms.setdefault(key, _new_term_acc(norm)), norm, seen_at)
            _fold_anchor_source(buckets, norm, seen_at)

    term_rows_out = []
    for acc in terms.values():
        acc["qualified_per_100_units"] = _per_100_units(acc["qualified_new"], acc["quota_units"])
        term_rows_out.append(acc)
    term_rows_out.sort(
        key=lambda acc: (-acc["qualified_new"], -acc["quota_units"], acc["term"], acc["platform"])
    )
    truncated = len(term_rows_out) > _MAX_TERM_ROWS
    term_rows_out = term_rows_out[:_MAX_TERM_ROWS]

    by_anchor_source = {}
    for source in sorted(buckets):
        bucket = buckets[source]
        by_anchor_source[source] = {
            "terms_count": len(bucket["terms"]),
            "session_rows": bucket["session_rows"],
            "first_seen": bucket["first_seen"],
            "last_seen": bucket["last_seen"],
            "search_calls": bucket["search_calls"],
            "youtube_search_calls": bucket["youtube_search_calls"],
            "quota_units": bucket["quota_units"],
            "qualified_new": bucket["qualified_new"],
            "qualified_per_100_units": _per_100_units(
                bucket["qualified_new"], bucket["quota_units"]
            ),
            "exhausted_sessions": bucket["exhausted_sessions"],
        }

    total_units = sum(acc["quota_units"] for acc in terms.values())
    total_qualified = sum(acc["qualified_new"] for acc in terms.values())
    return {
        **base,
        "status": "ok",
        "sessions_scanned": sessions_scanned,
        "sessions_used": sessions_used,
        "sessions_unparseable": sessions_unparseable,
        "schema_versions": schema_versions,
        "normalized_v1_rows": normalized_v1_rows,
        # 样本荒可见:本地现状只有 2 个会话,消费端必须能看出「数据不够别当真」。
        "low_sample": sessions_used < LOW_SAMPLE_SESSIONS,
        "low_sample_threshold": LOW_SAMPLE_SESSIONS,
        "terms": term_rows_out,
        "terms_count": len(term_rows_out),
        "terms_truncated": truncated,
        "by_anchor_source": by_anchor_source,
        "totals": {
            "search_calls": sum(acc["search_calls"] for acc in terms.values()),
            "youtube_search_calls": sum(acc["youtube_search_calls"] for acc in terms.values()),
            "quota_units": total_units,
            "qualified_new": total_qualified,
            "qualified_per_100_units": _per_100_units(total_qualified, total_units),
        },
    }


# ── 按 SKU 的词效台账(persona 知识库回填,迁移 306 的唯一数据源)────────────────
PER_SKU_TERM_PERFORMANCE_SCHEMA = "persona_term_performance_v1"

#: 高产词最多取 5 条(persona 载荷是知识摘要,不是全量台账)。
PER_SKU_TOP_TERMS = 5

_MAX_EXHAUSTED_TERMS = 50

# 与 _SESSIONS_SQL 同一套兼容层规矩(? 占位 / jsonb_exists / 表达式列带 AS);
# 差别只有一条:按 product_anchor.sku 精确匹配该 SKU 相关会话。
_SKU_SESSIONS_SQL = """
SELECT id AS session_id,
       created_at AS created_at,
       result_summary_json -> 'discovery_term_evidence' AS term_evidence
  FROM vkpi_kol_search_sessions
 WHERE jsonb_exists(result_summary_json, 'discovery_term_evidence')
   AND (result_summary_json -> 'discovery_term_evidence' -> 'product_anchor' ->> 'sku') = ?
   AND created_at >= NOW() - make_interval(days => ?)
 ORDER BY created_at ASC, id ASC
"""


def _fold_sku_sessions(
    rows: Any,
) -> tuple[dict[tuple[str, str, str], dict[str, Any]], dict[str, int]]:
    """把该 SKU 会话行折进 (platform, anchor_source, term) 台账,复用 v1/v2 shim。"""
    terms: dict[tuple[str, str, str], dict[str, Any]] = {}
    counters = {"sessions_scanned": 0, "sessions_used": 0, "sessions_unparseable": 0}
    for raw in rows:
        row = dict(raw)
        counters["sessions_scanned"] += 1
        evidence = _loads(row.get("term_evidence"))
        term_rows = evidence.get("terms") if isinstance(evidence, dict) else None
        if not isinstance(term_rows, list):
            counters["sessions_unparseable"] += 1
            continue
        counters["sessions_used"] += 1
        seen_at = _iso(row.get("created_at"))
        for term_row in term_rows:
            norm = normalize_term_row(term_row)
            if norm is None:
                continue
            key = (norm["platform"], norm["anchor_source"], norm["term"])
            _fold_term(terms.setdefault(key, _new_term_acc(norm)), norm, seen_at)
    return terms, counters


def _top_terms(accs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """高产词 top5:只收真换回过合格新人的词,按人数降序、效率降序。

    ``qualified_per_100_units`` 为 None(没烧配额)的词排在有效率数字之后——
    行内原始数字全保留,消费端可自行复核这个排序取舍。
    """
    productive = [acc for acc in accs if acc["qualified_new"] > 0]
    productive.sort(
        key=lambda acc: (
            -acc["qualified_new"],
            -(acc["qualified_per_100_units"] or 0.0),
            acc["term"],
            acc["platform"],
        )
    )
    return productive[:PER_SKU_TOP_TERMS]


def _exhausted_terms(accs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """已抓干词清单:任一会话标过 exhausted 的词,供改写端避开再烧配额。"""
    drained = [acc for acc in accs if acc["exhausted_sessions"] > 0]
    drained.sort(key=lambda acc: (acc["platform"], acc["term"]))
    return [
        {
            "term": acc["term"],
            "platform": acc["platform"],
            "anchor_source": acc["anchor_source"],
            "exhausted_sessions": acc["exhausted_sessions"],
            "qualified_new": acc["qualified_new"],
            "last_seen": acc["last_seen"],
        }
        for acc in drained[:_MAX_EXHAUSTED_TERMS]
    ]


def per_sku_term_performance(
    sku: Any, conn: Any = None, *, days: int = MAX_WINDOW_DAYS
) -> dict[str, Any]:
    """该 SKU 相关搜索会话的词效摘要:高产词 top5 + 已抓干词清单。纯读、零 LLM。

    persona 知识库回填(迁移 306 的 ``term_performance_json``)的唯一数据源;
    默认窗口取满一年——persona 知识长命,窄窗会把老 SKU 饿成假空账。
    失败方向与 :func:`aggregate_term_yield` 同款:读不出 → ``status='probe_failed'``,
    样本荒 → ``low_sample=True``,绝不伪装成一份可信的零产出台账。
    """
    window = max(1, min(_int(days, MAX_WINDOW_DAYS), MAX_WINDOW_DAYS))
    sku_text = _text(sku)
    base = {
        "schema": PER_SKU_TERM_PERFORMANCE_SCHEMA,
        "sku": sku_text,
        "window_days": window,
        "note": (
            "该 SKU 相关会话的逐词供给侧摘要(discovery_term_evidence 纯 SQL 聚合)。"
            "qualified_per_100_units 为 None = 该词没烧过配额,不是零产出。"
            "low_sample=True 时样本不足,别当结论用。"
        ),
    }
    if not sku_text:
        return {**base, "status": "no_sku", "reason": "empty_sku"}
    try:
        rows = (conn if conn is not None else get_conn()).execute(
            _SKU_SESSIONS_SQL, (sku_text, window)
        ).fetchall()
    except Exception as exc:
        logger.warning(
            "per_sku_term_performance_probe_failed sku=%s window_days=%s reason=%s",
            sku_text, window, str(exc)[:200], exc_info=True,
        )
        return {**base, "status": "probe_failed", "reason": "term_performance_probe_failed"}
    terms, counters = _fold_sku_sessions(rows)
    accs = list(terms.values())
    for acc in accs:
        acc["qualified_per_100_units"] = _per_100_units(acc["qualified_new"], acc["quota_units"])
    total_units = sum(acc["quota_units"] for acc in accs)
    total_qualified = sum(acc["qualified_new"] for acc in accs)
    return {
        **base,
        "status": "ok",
        **counters,
        "low_sample": counters["sessions_used"] < LOW_SAMPLE_SESSIONS,
        "low_sample_threshold": LOW_SAMPLE_SESSIONS,
        "terms_count": len(accs),
        "top_terms": _top_terms(accs),
        "exhausted_terms": _exhausted_terms(accs),
        "totals": {
            "quota_units": total_units,
            "qualified_new": total_qualified,
            "qualified_per_100_units": _per_100_units(total_qualified, total_units),
        },
    }


__all__ = [
    "DEFAULT_WINDOW_DAYS",
    "LOW_SAMPLE_SESSIONS",
    "MAX_WINDOW_DAYS",
    "PER_SKU_TERM_PERFORMANCE_SCHEMA",
    "PER_SKU_TOP_TERMS",
    "TERM_EVIDENCE_KEY",
    "TERM_YIELD_SCHEMA",
    "UNLABELED_ANCHOR_SOURCE",
    "aggregate_term_yield",
    "normalize_term_row",
    "per_sku_term_performance",
]
