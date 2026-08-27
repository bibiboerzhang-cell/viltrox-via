"""domains/kol/profile_discovery_evidence.py — 发现侧「用了什么词、各自产出多少」的埋点。

**证据埋点车道**(2026-08-27)。本模块零判定、零过滤、零花钱:只把这次搜索**已经发生**
的事实收拢成一份可落库、可 SELECT 的记录。红线:不触任何质量口径(新鲜度天数 /
required_terms / 器材证据 / 粉丝下限 / 检测器阈值),不写 viltrox_fit_score,不碰 rule_v0。

■ 为什么要有这个模块(今晚三次「修好了但生产链路够不着」的解药)
    今天的落库只有 ``discovery_round_plan`` 的**预报常量**(每轮固定 301 配额单位),
    既不落「实际用了哪几条检索词」,也不落「哪条词产出了哪个合格新人」。于是
    「泛词烧掉一半配额」这件事在库里没有任何痕迹——只能靠会滚掉的 INFO 日志反查。
    本模块把四样落成事实,让「新东西有没有被生产路径调用」变成一条 SELECT 能回答的问题:

      ① 实际用了哪几条检索词      ← provider metadata 的 ``provider_queries``(真发出去的)
      ② 产品锚是什么、来自哪条路径  ← plan/payload 里已有的 resolved_product/anchor 来源
      ③ 相关性判定时手里有几个字段  ← 对到手候选逐字段点名的填充率普查
      ④ 每条词产出几个合格新人、烧多少配额 ← 逐候选的检索词溯源 + 身份别名回连合格名单

■ 归因的诚实边界(宁可标「未归因」也不硬凑)
    YouTube 腿逐条 search.list 发变体,候选自带 ``discovery_query`` 溯源标 → ``per_item``。
    IG/TT 腿是一个 actor run 吃多条 query 后混合返回,平台不下发逐条溯源 → ``shared_round``:
    该腿的候选只记在轮次上,**绝不**按词平摊成小数冒充精确。
    合格新人回连用 ``canonical_creator_aliases``(与去重同一套身份口径),连不上的老实记
    ``qualified_unattributed_count``。

■ 配额口径(与 profile_discovery_rounds 的预报对账)
    search.list = 100 单位/变体;channels.list = 1 单位/轮。实际值直接读 provider metadata 的
    ``quota_units``(它已经按真发出去的变体数算);``overhead_units`` = 实际 − 100×变体数,
    正是那 1 个 channels.list。Apify 腿不吃 YouTube 配额,per-term 配额如实记 ``None``。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

TERM_EVIDENCE_SCHEMA = "discovery_term_evidence_v1"
TERM_EVIDENCE_KEY = "discovery_term_evidence"

# provider 逐条候选的检索词溯源标(account_search_discovery 的 YouTube 严格视频路写入)。
# 契约由 tests/test_discovery_term_evidence.py 钉住:写端改键名 = 测试红。
CANDIDATE_TERM_KEY = "discovery_query"

# YouTube Data API 计价(官方 quota cost):search.list=100,channels.list=1。
YOUTUBE_SEARCH_UNITS = 100

_MAX_TERMS = 24
_MAX_ROUNDS = 8
_MAX_TERM_LEN = 160

# 「相关性判定时手里有几个字段」的点名清单。每项 = (字段名, 候选上的别名们)。
# 这就是 profile_online_qualification._candidate_row 真正能填的那批字段:在线腿
# 常年只有 handle/display_name/bio/sample_title 有内容,其余恒空——普查把这件事
# 从「读代码才知道」变成「SELECT 一下就知道」。
JUDGMENT_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("handle", ("handle", "channel_id", "username")),
    ("display_name", ("display_name", "channel_name", "name")),
    ("bio", ("bio", "description")),
    ("sample_title", ("sample_title", "latest_video_title", "title")),
    ("followers", ("followers", "subscriber_count", "follower_count")),
    ("country", ("country",)),
    ("language", ("language",)),
    ("primary_topic", ("primary_topic",)),
    ("content_style", ("content_style",)),
    ("profile_text", ("profile_text",)),
    ("secondary_topics_json", ("secondary_topics_json",)),
    ("type_reason", ("type_reason",)),
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _code(value: Any) -> str:
    return _text(value).lower()


def _term(value: Any) -> str:
    return " ".join(_text(value).split())[:_MAX_TERM_LEN]


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _rows(value: Any) -> list[dict[str, Any]]:
    return [row for row in (value if isinstance(value, list) else []) if isinstance(row, dict)]


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _filled(value: Any) -> bool:
    """字段「手里有没有」的判据:空串 / None / 空表 / 0 都算没有(0 粉丝=未知,不算证据)。"""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    return bool(_text(value))


# ── ① 实际用了哪几条检索词 + ④ 配额 ─────────────────────────────────────────────
def _leg_actor_runs(platform: str, meta: dict[str, Any]) -> int:
    """这条腿这一轮真启动了几个 Apify actor run(不是预报,是 metadata 说的)。

    YouTube 走 Data API 快路径(actor_id 以 ``youtube-data-api/`` 打头)= 0 个 run、零花费;
    IG 多一个 profile-scraper run 当且仅当 metadata 说真请求过富化。
    """
    actor = _text(meta.get("actor_id"))
    if not actor or actor.startswith("youtube-data-api/"):
        return 0
    runs = 1
    if platform == "instagram" and _int(meta.get("profile_enrich_requested")) > 0:
        runs += 1
    return runs


def normalize_term_ledger(value: Any) -> list[dict[str, Any]]:
    """收下 provider metadata 里的逐词台账(检索词车道产出的供给侧真相)。

    有台账就以它为准:它知道每条词的**锚与锚来源**、真烧了多少配额、这条词抓干没有 ——
    这些是发词那一刻才知道的事,埋点侧只能搬运,不该自己再判一遍造出第二套真相。
    没有台账(IG/TT actor 路径、旧版 provider)→ 空表,埋点退回按 provider_queries 记账。
    """
    out: list[dict[str, Any]] = []
    for row in _rows(value)[:_MAX_TERMS]:
        term = _term(row.get("term"))
        if not term:
            continue
        entry = {
            "term": term,
            "anchor": _term(row.get("anchor")),
            "anchor_source": _code(row.get("anchor_source")),
            "quota_units": _int(row.get("quota_units")),
            "channels_new": _int(row.get("channels_new")),
            "exhausted": bool(row.get("exhausted")),
        }
        if _code(row.get("skipped")):
            entry["skipped"] = _code(row.get("skipped"))
        out.append(entry)
    return out


def observe_round(
    *,
    round_no: int,
    platform_results: Any,
    candidates: Any,
) -> dict[str, Any]:
    """把一次 provider 调用的「用词 / 配额 / 到手候选」收成一条轮次观测。纯函数零 IO。

    ``candidates`` 用 provider 刚返回的原始候选(收藏排除之前),这样「某条词捞回几个人」
    是 provider 侧的真相;后面各道闸丢了多少,漏斗记录(discovery_funnel)另有其账。
    """
    legs: list[dict[str, Any]] = []
    rows = _rows(candidates)
    by_platform: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_platform.setdefault(_code(row.get("platform")), []).append(row)
    for entry in _rows(platform_results):
        platform = _code(entry.get("platform"))
        if not platform:
            continue
        meta = _dict(entry.get("metadata"))
        terms = [_term(item) for item in (meta.get("provider_queries") or []) if _term(item)]
        quota_actual = _int(meta.get("quota_units"))
        actor_runs = _leg_actor_runs(platform, meta)
        leg_rows = by_platform.get(platform) or []
        tagged: dict[str, int] = {}
        untagged = 0
        for row in leg_rows:
            tag = _term(row.get(CANDIDATE_TERM_KEY))
            if tag:
                tagged[tag] = tagged.get(tag, 0) + 1
            else:
                untagged += 1
        ledger = normalize_term_ledger(meta.get("term_ledger"))
        legs.append({
            "platform": platform,
            "status": _code(entry.get("status")),
            "terms": terms[:_MAX_TERMS],
            "term_count": len(terms),
            # 有逐词台账就一并落库(锚/锚来源/逐词配额/抓干与否),没有则空表。
            "term_ledger": ledger,
            # 实际配额直接来自 provider metadata;Apify 腿不吃 YouTube 配额 → 0。
            "quota_units_actual": quota_actual,
            # 实际 − 100×变体数 = channels.list 那 1 个单位(诚实余项,不硬塞进某条词)。
            "quota_overhead_units": max(0, quota_actual - YOUTUBE_SEARCH_UNITS * len(terms)),
            "apify_actor_runs": actor_runs,
            "candidates_returned": len(leg_rows),
            "candidates_by_term": dict(sorted(tagged.items())),
            "candidates_untagged": untagged,
            "attribution": "per_item" if leg_rows and not untagged else (
                "shared_round" if leg_rows else "no_candidates"
            ),
        })
    return {
        "round_no": max(1, _int(round_no) or 1),
        "platforms": sorted({row["platform"] for row in legs}),
        "legs": legs,
        "quota_units_actual": sum(row["quota_units_actual"] for row in legs),
        "apify_actor_runs": sum(row["apify_actor_runs"] for row in legs),
        "candidates_returned": len(rows),
        # 本轮到手候选的字段普查(③)。逐轮存,便于看「翻页越深字段越稀」。
        "field_census": field_census(rows),
    }


# ── ② 产品锚是什么、来自哪条路径 ───────────────────────────────────────────────
def operator_anchor_inputs(payload: Any) -> dict[str, Any]:
    """planner 改写 payload **之前**先拍一张:操作员自己给了什么锚。

    必须在 pipeline 顶部调用——planner 会把解析出的 SKU 回填进 ``payload['product_sku']``,
    之后就分不清「操作员点的产品」和「模型猜的产品」了。
    """
    raw = _dict(payload)
    return {
        "operator_product_sku": _text(raw.get("product_sku") or raw.get("productSku"))[:64],
        "operator_query": _term(raw.get("query_text") or raw.get("input") or raw.get("query")),
    }


def product_anchor_record(
    *,
    payload: Any,
    operator_anchor: Any,
    effective_query: str,
) -> dict[str, Any]:
    """产品锚的真相:是什么、哪条路径给的、锚词有哪些。全部搬运既有事实,零推断。"""
    from app.domains.kol.profile_recall_match_evidence import (
        product_evidence_terms,
        query_evidence_terms,
    )

    body = _dict(payload)
    given = _dict(operator_anchor)
    plan = _dict(body.get("llm_query_plan"))
    resolved = _dict(plan.get("resolved_product") or body.get("resolved_product"))
    sku = _text(resolved.get("sku"))
    operator_sku = _text(given.get("operator_product_sku"))
    if sku:
        kind = "sku"
        source = "operator_selected_sku" if operator_sku else "resolved_from_query"
    elif _text(plan.get("product_anchor")) or body.get("product_focus"):
        kind = "brand_category"
        source = _code(plan.get("reason")) or "plan_product_focus"
    else:
        kind = "none"
        source = "unanchored"
    anchor_terms = product_evidence_terms(resolved) if resolved else []
    if not anchor_terms:
        anchor_terms = query_evidence_terms(given.get("operator_query"))
    return {
        "kind": kind,
        "source": source,
        "sku": sku[:64],
        "model_name": _text(resolved.get("model_name"))[:120],
        "series": _text(resolved.get("series"))[:64],
        "category_main": _text(resolved.get("category_main"))[:64],
        # 锚词 = 判「某条检索词有没有带锚」的唯一依据,落库让判据本身可复核。
        "anchor_terms": [_term(term) for term in anchor_terms][:_MAX_TERMS],
        "plan_source": _code(body.get("query_plan_source")),
        "plan_provider": _code(plan.get("provider")),
        "plan_model": _code(plan.get("model")),
        "plan_reason": _code(plan.get("reason")),
        "evidence_anchor_source": _code(plan.get("evidence_anchor_source") or plan.get("anchor_source")),
        "fallback_used": bool(plan.get("fallback_used")),
        "effective_search_query": _term(effective_query),
        "operator_query": _term(given.get("operator_query")),
    }


def term_is_anchored(term: Any, anchor_terms: Any) -> bool:
    """这条检索词带没带产品锚。锚词表为空 → 一律 False(没锚可带,不假装带了)。"""
    haystack = _code(term)
    if not haystack:
        return False
    return any(_code(anchor) in haystack for anchor in (anchor_terms or []) if _code(anchor))


# ── ③ 相关性判定时手里有几个字段 ───────────────────────────────────────────────
def field_census(candidates: Any) -> dict[str, Any]:
    """对到手候选逐字段点名:哪几个字段真有内容、平均几个、分布如何。

    诚实空态:一个候选都没有时给零值 + 空分布,不省略键(SELECT 侧不用兼容两种形状)。
    """
    rows = _rows(candidates)
    by_field = {name: 0 for name, _aliases in JUDGMENT_FIELDS}
    histogram: dict[str, int] = {}
    total_present = 0
    for row in rows:
        present = 0
        for name, aliases in JUDGMENT_FIELDS:
            if any(_filled(row.get(alias)) for alias in aliases):
                by_field[name] += 1
                present += 1
        total_present += present
        histogram[str(present)] = histogram.get(str(present), 0) + 1
    return {
        "candidates": len(rows),
        "fields_checked": [name for name, _aliases in JUDGMENT_FIELDS],
        "fields_checked_count": len(JUDGMENT_FIELDS),
        "by_field": by_field,
        "fields_present_avg": round(total_present / len(rows), 2) if rows else 0.0,
        "fields_present_histogram": dict(sorted(histogram.items(), key=lambda pair: int(pair[0]))),
    }


def _merge_census(rounds: list[dict[str, Any]]) -> dict[str, Any]:
    by_field = {name: 0 for name, _aliases in JUDGMENT_FIELDS}
    histogram: dict[str, int] = {}
    candidates = 0
    weighted = 0.0
    for entry in rounds:
        census = _dict(entry.get("field_census"))
        count = _int(census.get("candidates"))
        candidates += count
        weighted += float(census.get("fields_present_avg") or 0.0) * count
        for name in by_field:
            by_field[name] += _int(_dict(census.get("by_field")).get(name))
        for bucket, hits in _dict(census.get("fields_present_histogram")).items():
            histogram[str(bucket)] = histogram.get(str(bucket), 0) + _int(hits)
    return {
        "candidates": candidates,
        "fields_checked": [name for name, _aliases in JUDGMENT_FIELDS],
        "fields_checked_count": len(JUDGMENT_FIELDS),
        "by_field": by_field,
        "fields_present_avg": round(weighted / candidates, 2) if candidates else 0.0,
        "fields_present_histogram": dict(sorted(histogram.items(), key=lambda pair: int(pair[0]))),
    }


# ── ④ 每条词产出几个合格新人 ───────────────────────────────────────────────────
def _aliases(row: Any) -> set[str]:
    from app.domains.kol.identity import canonical_creator_aliases

    try:
        return canonical_creator_aliases(row if isinstance(row, dict) else {})
    except Exception:  # 身份归一失败不许拖垮埋点;这条就是「连不上」
        logger.debug("term_evidence_alias_failed", exc_info=True)
        return set()


def attribute_qualified(
    *,
    observed_candidates: Any,
    accepted_items: Any,
) -> dict[str, Any]:
    """把合格新人回连到「是哪条检索词捞回来的」。

    连接键 = ``canonical_creator_aliases``(与发现侧去重同一套身份口径,不另造一套)。
    连不上 / 候选本来就没溯源标的,老实计入 ``unattributed``,不按词平摊。
    """
    alias_to_term: dict[str, tuple[str, str]] = {}
    for row in _rows(observed_candidates):
        term = _term(row.get(CANDIDATE_TERM_KEY))
        if not term:
            continue
        platform = _code(row.get("platform"))
        for alias in _aliases(row):
            alias_to_term.setdefault(alias, (platform, term))
    qualified: dict[tuple[str, str], int] = {}
    unattributed = 0
    accepted = _rows(accepted_items)
    for item in accepted:
        hit: tuple[str, str] | None = None
        for alias in _aliases(item):
            if alias in alias_to_term:
                hit = alias_to_term[alias]
                break
        if hit is None:
            unattributed += 1
            continue
        qualified[hit] = qualified.get(hit, 0) + 1
    return {
        "qualified": qualified,
        "accepted_count": len(accepted),
        "unattributed_count": unattributed,
        "tagged_alias_count": len(alias_to_term),
    }


# ── 总装 ───────────────────────────────────────────────────────────────────────
def build_term_evidence(
    *,
    lane: str,
    anchor: Any,
    rounds: Any,
    observed_candidates: Any,
    accepted_items: Any = None,
    quota_forecast_units: Any = None,
) -> dict[str, Any]:
    """把四样拼成一条可落库记录。纯函数零 IO,失败方向:宁可空态也不杜撰。"""
    anchor_face = _dict(anchor)
    anchor_terms = anchor_face.get("anchor_terms") or []
    round_rows = [_dict(entry) for entry in (rounds if isinstance(rounds, list) else [])][:_MAX_ROUNDS]
    attribution = attribute_qualified(
        observed_candidates=observed_candidates,
        accepted_items=accepted_items,
    )
    qualified_map = attribution["qualified"]

    terms: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in round_rows:
        round_no = _int(entry.get("round_no")) or 1
        for leg in _rows(entry.get("legs")):
            platform = _code(leg.get("platform"))
            per_item = _code(leg.get("attribution")) == "per_item"
            by_term = _dict(leg.get("candidates_by_term"))
            ledger = {row["term"]: row for row in normalize_term_ledger(leg.get("term_ledger"))}
            for term in (leg.get("terms") or [])[:_MAX_TERMS]:
                key = (platform, _term(term))
                booked = ledger.get(key[1])
                row = terms.setdefault(key, {
                    "term": key[1],
                    "platform": platform,
                    # 有逐词台账就用它的锚判定(发词那一刻的真相);没有才回落词形比对。
                    "anchored": (
                        _code(booked.get("anchor_source")) not in ("", "unanchored_legacy_chunk")
                        if booked else term_is_anchored(key[1], anchor_terms)
                    ),
                    **({"anchor": booked.get("anchor"), "anchor_source": booked.get("anchor_source")}
                       if booked else {}),
                    "rounds": [],
                    "search_calls": 0,
                    "quota_units": 0,
                    "apify_actor_runs": 0,
                    "candidates_returned": 0,
                    "attribution": "per_item" if per_item else "shared_round",
                })
                row["rounds"].append(round_no)
                row["search_calls"] += 1
                # 逐词台账的配额优先(它知道这条词到底发没发出去);否则每条已发变体记 100,
                # Apify 腿不吃 YouTube 配额 → 0。
                row["quota_units"] += (
                    _int(booked.get("quota_units")) if booked
                    else (YOUTUBE_SEARCH_UNITS if _int(leg.get("quota_units_actual")) else 0)
                )
                row["candidates_returned"] += _int(by_term.get(key[1]))
                if booked:
                    row["exhausted"] = bool(booked.get("exhausted"))
                    if booked.get("skipped"):
                        row["skipped"] = booked["skipped"]
                if not per_item:
                    row["attribution"] = "shared_round"
    for key, count in qualified_map.items():
        row = terms.get(key)
        if row is None:
            row = terms.setdefault(key, {
                "term": key[1],
                "platform": key[0],
                "anchored": term_is_anchored(key[1], anchor_terms),
                "rounds": [],
                "search_calls": 0,
                "quota_units": 0,
                "apify_actor_runs": 0,
                "candidates_returned": 0,
                "attribution": "per_item",
            })
        row["qualified_new"] = _int(row.get("qualified_new")) + count
    term_rows = []
    for row in terms.values():
        row["rounds"] = sorted(set(row["rounds"]))
        row.setdefault("qualified_new", 0)
        # shared_round 的腿无法逐词归因产出:老实写 None,不拿轮次总数冒充某条词的战绩。
        if row["attribution"] == "shared_round":
            row["candidates_returned"] = None
        term_rows.append(row)
    term_rows.sort(
        key=lambda row: (-_int(row.get("qualified_new")), -_int(row.get("quota_units")), row["term"])
    )
    term_rows = term_rows[:_MAX_TERMS]

    quota_actual = sum(_int(entry.get("quota_units_actual")) for entry in round_rows)
    forecast = _int(quota_forecast_units) if quota_forecast_units is not None else None
    return {
        "schema": TERM_EVIDENCE_SCHEMA,
        "lane": _code(lane) or "unknown",
        "product_anchor": anchor_face,
        "terms": term_rows,
        "terms_count": len(term_rows),
        "unanchored_terms": sorted({row["term"] for row in term_rows if not row["anchored"]}),
        "anchored_terms_count": sum(1 for row in term_rows if row["anchored"]),
        "rounds": round_rows,
        "provider_rounds": len(round_rows),
        "quota": {
            "youtube_units_actual": quota_actual,
            "youtube_units_forecast": forecast,
            "forecast_delta_units": (forecast - quota_actual) if forecast is not None else None,
            "apify_actor_runs_actual": sum(_int(entry.get("apify_actor_runs")) for entry in round_rows),
            # 无锚词烧掉的配额——「泛词烧掉一半配额」这件事从此有真数,不用翻日志。
            "unanchored_units": sum(
                _int(row.get("quota_units")) for row in term_rows if not row["anchored"]
            ),
        },
        "candidates_returned": sum(_int(entry.get("candidates_returned")) for entry in round_rows),
        "qualified_new_total": sum(_int(row.get("qualified_new")) for row in term_rows),
        "qualified_accepted_count": _int(attribution.get("accepted_count")),
        "qualified_unattributed_count": _int(attribution.get("unattributed_count")),
        "field_census": _merge_census(round_rows),
    }


def planned_youtube_variants(query: Any) -> int:
    """这次搜索 YouTube 腿**打算**发几条变体(确定性纯函数,零 IO、零花费)。

    只服务配额预报:此前预报写死 3 条(301 单位),而真发出去常常只有 2 条(201)——
    50% 高估会让轮次守门按虚高的账提前收手。读不到就退回 1(下限,绝不虚报)。
    """
    text = _term(query)
    if not text:
        return 0
    try:
        from app.services.intelligence.account_search_terms import (
            _youtube_search_query_variants,
        )

        return max(1, len(_youtube_search_query_variants(text)))
    except Exception:
        logger.debug("planned_youtube_variants_unavailable", exc_info=True)
        return 1
